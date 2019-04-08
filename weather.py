# -*- coding: utf-8 -*

"""
Module for reading in weather conditions from Weather Underground.
"""

import json
import time
import numpy
import logging
import urllib2
from datetime import datetime, timedelta

from expiring_cache import expiring_cache

__version__ = '0.6'
__all__ = ['get_current_conditions', 'get_three_day_history', 'get_current_temperature', 
           'get_daily_et', '__version__']


# Logger instance
_LOGGER = logging.getLogger('__main__')


# Rate limiter
class _RateLimiter(object):
    """
    Class to help make sure that the various calls to the WUnderground API don't exceed 
    the free rate limit of 5 queries per minute.  This class can also be adjusted to 
    work with other rate limits as well.
    """
    
    def __init__(self, requests_per_minute=5):
        self.requests_per_minute = float(requests_per_minute)
        
        self._requests = []
        
    def clearToSend(self, block=True):
        """
        Find out if sending a request now would be allowable.  Return True
        if it is, False otherwise.  If 'block' is set to True, the function
        blocks until it is clear to make a request again.
        """
        
        # Get the current time
        tNow = time.time()
        
        # Find out how many requests have been make in the last minute and make of list
        # of requests that should be purged
        requestCount = 0
        requestsToRemove = []
        for t in self._requests:
            if tNow - t <= 60.0:
                requestCount += 1
            else:
                requestsToRemove.append(t)
                
        # Clean out the old requests
        for t in requestsToRemove:
            idx = self._requests.index(t)
            del self._requests[idx]
            
        # Can we make another request?
        clear = False
        if requestCount < self.requests_per_minute:
            ## Yes, update the requests list
            self._requests.append( tNow )
            clear = True
        else:
            if block:
                ## Sleep for a bit and try again
                _LOGGER.warning('WUnderground rate limiter in effect')
                time.sleep(5)
                while not self.clearToSend(block=False):
                    time.sleep(5)
                    
                _LOGGER.info('WUnderground rate limiter cleared')
                clear = True
            else:
                ## Nope
                clear = False
                
        return clear


# Create the rate limiter
_rl = _RateLimiter()


def get_current_conditions(pws, timeout=30):
    """
    Get the current conditions of the personal weather station using WUnderground.
    """
    
    # Get the URL
    url = "https://api.weather.com/v2/pws/observations/current?apiKey=6532d6454b8aa370768e63d6ba5a832e&stationId=%s&format=json&units=e" % pws
    
    # Check the rate limiter
    _rl.clearToSend()
    
    try:
        uh = urllib2.urlopen(url, None, timeout)
    except Exception as e:
        raise RuntimeError("Failed to connect to WUnderground for current conditions: %s" % str(e))
    else:
        data = json.loads(uh.read())
        uh.close()
        
    return data


def get_three_day_history(pws, timeout=30):
    """
    Get the weather history for the last three days from a personal weather station using
    WUnderground.
    """
    
    # Get the URL
    url = "https://api.weather.com/v2/pws/observations/all/3day?apiKey=6532d6454b8aa370768e63d6ba5a832e&stationId=%s&format=json&units=e" % pws
    
    # Check the rate limiter
    _rl.clearToSend()
    
    try:
        uh = urllib2.urlopen(url, None, timeout)
    except Exception as e:
        raise RuntimeError("Failed to connect to WUnderground for three-day history: %s" % str(e))
    else:
        data = json.loads(uh.read())
        uh.close()
        
    return data


@expiring_cache(maxage=1800)
def get_current_temperature(pws, timeout=30):
    """
    Get the current temperature in degrees Fahrenheit using the WUnderground 
    API.
    """
    
    data = get_current_conditions(pws, timeout=timeout)
    
    try:
        current = data['observations'][0]
        
        tNow = float(current['imperial']['temp'])
        _LOGGER.debug('Current temperature is %.1f F for %s', tNow, pws)
    except Exception as e:
        raise RuntimeError("Failed to get current temperature: %s" % str(e))
        
    return tNow


def _T(TF):
    """
    Temperature in F to C.
    """
    
    return (TF-32.0)*5.0/9.0

def _u2(uMPH, height=2.0):
    """
    Wind speed in mph measured at height (in m) to m/s at 2 m.
    """
    
    u = uMPH*0.447
    u *= 4.87 / numpy.log(67.8*height - 5.42)
    return u

def _Delta(Tmean):
    """
    Slope of saturation water vapor curve (in kPa/C) at temperature T (in C).
    """
    
    d = 17.27*Tmean / (Tmean + 237.3)
    d = 4098.0*0.6108*numpy.exp(d)
    d = d / (Tmean + 237.3)**2
    return d

def _P(elev):
    """
    Atmospheric pressure (in kPa) as a function of elevation (in m).
    """
    
    p = (293 - 0.0065*elev)/293.0
    p = 101.3*p**5.26
    return p

def _Elevation(P):
    """
    Effective elevation (in m) as a function of the atmospheric pressure (in kPa)
    [inverse of P(elev)].
    """
    
    elev = (P/101.3)**(1.0/5.26)
    elev = (293.0 - elev*293.0) / 0.0065
    return elev

def _gamma(P):
    """
    Psychrometric constant (in kPa/C) as a function of atmospheric pressure (in kPa).
    """
    
    return 0.000665*P

def _DT(Tmean, P, u2, Cd=0.34):
    """
    Delta term for the radiation component.
    """
    
    d = _Delta(Tmean)
    g = _gamma(P)
    return d / (d + g*(1+Cd*u2))

def _PT(Tmean, P, u2, Cd=0.34):
    """
    Psi term for the wind component.
    """
    
    d = _Delta(Tmean)
    g = _gamma(P)
    return g / (d + g*(1+Cd*u2))

def _TT(Tmean, u2, Cn=900.0):
    """
    Temperature term for the wind component.
    """
    
    return u2*Cn/(Tmean + 273.0)

def _eT(T):
    """
    Saturation vapor pressure (in kPa) of air at temperature T (in C).
    """
    
    return 0.6108*numpy.exp(17.27*T/(T+237.3))

def _eS(Tmin, Tmax):
    """
    Mean saturation vapor pressure (in kPa) of air in the temperture range of Tmin to 
    Tmax (in C).
    """
    
    return 0.5*_eT(Tmin) + 0.5*_eT(Tmax)

def _eA(Tmin, Tmax, RHmin, RHmax):
    """
    Actual mean vapor pressure (in kPa) of air at temperature range Tmin to Tmax (in C) 
    and relative humidity range RHmin to RHmax (as a percentage).
    """
    
    return 0.5*_eT(Tmin)*RHmax/100.0 + 0.5*_eT(Tmax)*RHmin/100.0
    
def _Ra(lat, J):
    """
    Solar radiation (in MJ/m^2/d) from the latitude (in deg) and the day-of-the-year.
    """
    
    lat = lat*numpy.pi/180.0
    if type(J) is datetime:
        J = float(J.strftime("%j"))
        
    dR = 1.0 + 0.033*numpy.cos(2*numpy.pi*J/365.0)
    d = 0.409*numpy.sin(2*numpy.pi*J/365.0 - 1.39)
    Gs = 0.0820
    
    # Sunset hour angle
    omega = numpy.arccos(-numpy.tan(lat)*numpy.tan(d))
    
    r = omega*numpy.sin(lat)*numpy.sin(d) + numpy.cos(lat)*numpy.cos(d)*numpy.sin(omega)
    r = 24*60/numpy.pi * Gs*dR*r
    return r

def _Rso(lat, elev, J):
    """
    Clear sky solar radiation (in MJ/m^2/d) from latitude (in deg), elevation (in m), and
    the day-of-the-year.
    """
    
    return (0.75 + 2e-5*elev)*_Ra(lat, J)

def _Rns(R=None, lat=0.0, elev=0.0, J=0.0, albedo=0.23):
    """
    Net solar radiation (in MJ/m^2/d) from the mean daily solar radiation in (W/m^2/d).
    """
    
    if R is None:
        R = _Rso(lat, elev, J)
    else:
        R = R*0.0864
        
    return (1.0-albedo)*R

def _Rnl(Tmin, Tmax, RHmin, RHmax, lat, elev, J, R=None):
    """
    Net outgoing long wave solar radiation (in MJ/m^2/d) from the tempereture range (in C), 
    latitude (in deg), elevation (in m), day-of-the-year, and the mean daily solar radiation
    (in W/m^2/d).
    """
    
    e = _eA(Tmin, Tmax, RHmin, RHmax)
    rs = _Rso(lat, elev, J)
    if R is None:
        R = rs*1.0
    else:
        R = R*0.0864
        
    t1 = 4.903e-9*( 0.5*(Tmin+273.16)**4 + 0.5*(Tmax+273.16)**4 )
    t2 = 0.34 - 0.14*numpy.sqrt(e)
    t3 = 1.35*R/rs - 0.35
    return t1*t2*t3

def _Rn(Tmin, Tmax, RHmin, RHmax, lat, elev, J, R=None, albedo=0.23):
    """
    Net radiation (in mm equivalent evaporation) from the tempereture range (in C), 
    latitude (in deg), elevation (in m), day-of-the-year, and the mean daily solar 
    radiation (in W/m^2/d).
    """
    
    return 0.408*(_Rns(R=R, lat=lat, elev=elev, J=J, albedo=albedo) - _Rnl(Tmin, Tmax, RHmin, RHmax, lat, elev, J, R))

def _ET(Tmin, Tmax, u2, RHmin, RHmax, lat, elev, J, R=None, Cn=900.0, Cd=0.34, albedo=0.23):
    """
    Evapotransperation value (in mm/d) as a function of the temperature range Tmin to Tmax
    (in C), the wind speed (in m/s), the relative humidity range RHmin ot RHmax (as a
    percentage), the latitude (in deg), the elevation (in m), the day-of-the-year, and
    the mean daily solar radiation (in W/m^2/d).
    """
    
    Tmean = 0.5*Tmin + 0.5*Tmax
    p = _P(elev)
    
    r = _DT(Tmean, p, u2, Cd=Cd) * _Rn(Tmin, Tmax, RHmin, RHmax, lat, elev, J, R, albedo=albedo)
    w = _PT(Tmean, p, u2, Cd=Cd) * _TT(Tmean, u2, Cn=Cn) * (_eS(Tmin, Tmax) - _eA(Tmin, Tmax, RHmin, RHmax))
    return r + w

def get_daily_et(pws, Cn=900.0, Cd=0.34, albedo=0.23, inches=True, timeout=30):
    """
    Estimate the evapotranpsersion loss (in mm or inches) for the last 24 hours using data
    from the specified WUnderground weather station.  If the loss is wanted in mm, set
    the `inches` keyword to False.
    """
    
    # Weather station latitude and elevation above sea level (in m) via the current 
    # conditions
    data = get_current_conditions(pws, timeout=timeout)
    lat = float(data['observations'][0]['lat'])                         # degrees
    elev = float(data['observations'][0]['imperial']['elev']) * 0.3048  # ft -> m
    
    # Weather conditions for the past 24 hours
    dtNow = datetime.utcnow()
    dtStart = dtNow - timedelta(days=1)
    data = get_three_day_history(pws, timeout=timeout)
    
    t, h, w, p, r = [], [], [], [], []
    try:
        history = data['observations']
        
        for day in history:
            dt = datetime.utcfromtimestamp(day['epoch'])
            if dt < dtStart:
                continue
                
            t.append( _T(float(day['imperial']['tempAvg'])) )        # F -> C
            h.append( float(day['humidityAvg']) )                   # %
            w.append( _u2(float(day['imperial']['windspeedAvg'])) )  # MPH -> m/s
            p.append( float(day['imperial']['precipTotal'])*25.4 )  # in -> mm
            try:
                r.append( float(day['solarRadiationHigh']) )        # W/m^2
            except ValueError:
                r.append( None )
                
        ## Convert the total rainfall to Delta_{rain}
        dp = [0.0,]
        for i in xrange(1, len(p)):
            dp.append( p[i]-p[i-1] )
            if dp[-1] < 0:
                dp[-1] = 0.0
        p = dp
        
    except Exception as e:
        pass
        
    # Compute the min, max, and average values (where needed)
    Tmin = min(t)
    Tmax = max(t)
    RHmin = min(h)
    RHmax = max(h)
    w = sum(w)/len(w)
    try:
        r = sum(r)/len(r)
    except TypeError:
        r = None
        
    # Report - part 1
    _LOGGER.debug("Temperature: %.1f to %.1f C", Tmin, Tmax)
    _LOGGER.debug("Relative humidity: %.0f%% to %.0f%%", RHmin, RHmax)
    _LOGGER.debug("Average wind speed: %.1f m/s",  w)
    _LOGGER.debug("Elevation above sea level: %.1f m",  elev)
    _LOGGER.debug("Total rainfall: %.2f mm", sum(p))
    try:
        _LOGGER.debug("Average solar radiation: %.1f W/m^2/d", r)
    except TypeError:
        _LOGGER.debug("Average solar radiation: calculated from latitude and day-of-the-year")
        
    # Compute the evapotranspiration loss...
    loss = _ET(Tmin, Tmax, w, RHmin, RHmax, lat, elev, dtStart, R=r, Cn=Cn, Cd=Cd, albedo=albedo)
    _LOGGER.info("ET loss: %.2f mm", loss)
    # ... and correct for the amount of rainfall received.
    loss -= sum(p)
    loss = max([0.0, loss])
    _LOGGER.info("ET loss, less rainfall received: %.2f mm", loss)
        
    # Convert, if needed, and return
    if inches:
        loss = loss / 25.4
    return loss