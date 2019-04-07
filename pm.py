"""
Moduole for calculating the Penman-Monteith equation based using values from WUnderground 
weather station.  Based on:

Step by Step Calculation of the Penman-Monteith Evapotranspiration (FAO-56 Method)
  Lincoln Zotarelli, Michael D. Dukes, Consuelo C. Romero, Kati W. Migliaccio, 
  and Kelly T. Morgan

http://edis.ifas.ufl.edu/pdffiles/ae/ae45900.pdf
"""

import numpy
import logging
from datetime import datetime, timedelta

from weather import getCurrentConditions, getThreeDayHistory

__version__ = '0.1'
__all__ = ['T', 'u2', 'Delta', 'P', 'Elevation', 'gamma', 'DT', 'PT', 'TT', 'eT', 'eS', 'eA', 
           'Ra', 'Rso', 'Rns', 'Rnl', 'Rn', 'ET', 'getET', '__version__']


# Logger instance
pmLogger = logging.getLogger('__main__')


def T(TF):
    """
    Temperature in F to C.
    """
    
    return (TF-32.0)*5.0/9.0

def u2(uMPH, height=2.0):
    """
    Wind speed in mph measured at height (in m) to m/s at 2 m.
    """
    
    u = uMPH*0.447
    u *= 4.87 / numpy.log(67.8*height - 5.42)
    return u

def Delta(Tmean):
    """
    Slope of saturation water vapor curve at temperature T (in C).
    """
    
    d = 17.27*Tmean / (Tmean + 237.3)
    d = 4098.0*0.6108*numpy.exp(d)
    d = d / (Tmean + 237.3)**2
    return d

def P(elev):
    """
    Atmospheric pressure (in kPa) as a function of elevation (in m).
    """
    
    p = (293 - 0.0065*elev)/293.0
    p = 101.3*p**5.26
    return p

def Elevation(P):
    """
    Effective elevation (in m) as a function of the atmospheric pressure (in kPa)
    [inverse of P(elev)].
    """
    
    elev = (P/101.3)**(1.0/5.26)
    elev = (293.0 - elev*293.0) / 0.0065
    return elev

def gamma(P):
    """
    Psychrometric constant (in kPa/C) as a function of atmospheric pressure (in kPa).
    """
    
    return 0.000665*P

def DT(Tmean, P, u2):
    """
    Delta term for the radiation component.
    """
    
    d = Delta(Tmean)
    g = gamma(P)
    return d / (d + g*(1+0.34*u2))

def PT(Tmean, P, u2):
    """
    Psi term for the wind component.
    """
    
    d = Delta(Tmean)
    g = gamma(P)
    return g / (d + g*(1+0.34*u2))

def TT(Tmean, u2):
    """
    Temperature term for the wind component.
    """
    
    return u2*900.0/(Tmean + 273.0)

def eT(T):
    """
    Saturation vapor pressure (in kPa) of air at temperature T (in C).
    """
    
    return 0.6108*numpy.exp(17.27*T/(T+237.3))

def eS(Tmin, Tmax):
    """
    Mean saturation vapor pressure (in kPa) of air in the temperture range of Tmin to 
    Tmax (in C).
    """
    
    return 0.5*eT(Tmin) + 0.5*eT(Tmax)

def eA(Tmin, Tmax, RHmin, RHmax):
    """
    Actual mean vapor pressure (in kPa) of air at temperature range Tmin to Tmax (in C) 
    and relative humidity range RHmin to RHmax (as a percentage).
    """
    
    return 0.5*eT(Tmin)*RHmax/100.0 + 0.5*eT(Tmax)*RHmin/100.0
    
def Ra(lat, J):
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

def Rso(lat, elev, J):
    """
    Clear sky solar radiation (in MJ/m^2/d) from latitude (in deg), elevation (in m), and
    the day-of-the-year.
    """
    
    return (0.75 + 2e-5*elev)*Ra(lat, J)

def Rns(R=None, a=0.23, lat=0.0, elev=0.0, J=0.0):
    """
    Net solar radiation (in MJ/m^2/d) from the mean daily solar radiation in (W/m^2/d).
    """
    
    if R is None:
        R = Rso(lat, elev, J)
    else:
        R = R*0.0864
        
    return (1.0-a)*R

def Rnl(Tmin, Tmax, RHmin, RHmax, lat, elev, J, R=None):
    """
    Net outgoing long wave solar radiation (in MJ/m^2/d) from the tempereture range (in C), 
    latitude (in deg), elevation (in m), day-of-the-year, and the mean daily solar radiation
    (in W/m^2/d).
    """
    
    e = eA(Tmin, Tmax, RHmin, RHmax)
    rs = Rso(lat, elev, J)
    if R is None:
        R = rs*1.0
    else:
        R = R*0.0864
        
    t1 = 4.903e-9*( 0.5*(Tmin+273.16)**4 + 0.5*(Tmax+273.16)**4 )
    t2 = 0.34 - 0.14*numpy.sqrt(e)
    t3 = 1.35*R/rs - 0.35
    return t1*t2*t3

def Rn(Tmin, Tmax, RHmin, RHmax, lat, elev, J, R=None):
    """
    Net radiation (in mm equivalent evaporation) from the tempereture range (in C), 
    latitude (in deg), elevation (in m), day-of-the-year, and the mean daily solar 
    radiation (in W/m^2/d).
    """
    
    return 0.408*(Rns(R=R, lat=lat, elev=elev, J=J) - Rnl(Tmin, Tmax, RHmin, RHmax, lat, elev, J, R))

def ET(Tmin, Tmax, u2, RHmin, RHmax, lat, elev, J, R=None):
    """
    Evapotransperation value (in mm/d) as a function of the temperature range Tmin to Tmax
    (in C), the wind speed (in m/s), the relative humidity range RHmin ot RHmax (as a
    percentage), the latitude (in deg), the elevation (in m), the day-of-the-year, and
    the mean daily solar radiation (in W/m^2/d).
    """
    
    Tmean = 0.5*Tmin + 0.5*Tmax
    p = P(elev)
    
    r = DT(Tmean, p, u2) * Rn(Tmin, Tmax, RHmin, RHmax, lat, elev, J, R)
    w = PT(Tmean, p, u2) * TT(Tmean, u2) * (eS(Tmin, Tmax) - eA(Tmin, Tmax, RHmin, RHmax))
    return r + w

def getET(pws, inches=True, timeout=30):
    """
    Estimate the evapotranpsersion loss (in mm or inches) for the last 24 hours using data
    from the specified WUnderground weather station.  If the loss is wanted in mm, set
    the `inches` keyword to False.
    """
    
    # Weather station latitude and elevation above sea level (in m) via the current 
    # conditions
    data = getCurrentConditions(pws, timeout=timeout)
    lat = float(data['observations'][0]['lat'])                         # degrees
    elev = float(data['observations'][0]['imperial']['elev']) * 0.3048  # ft -> m
    
    # Weather conditions for the past 24 hours
    dtNow = datetime.utcnow()
    dtStart = dtNow - timedelta(days=1)
    data = getThreeDayHistory(pws, timeout=timeout)
    
    t, h, w, p, r = [], [], [], [], []
    try:
        history = data['observations']
        
        for day in history:
            dt = datetime.utcfromtimestamp(day['epoch'])
            if dt < dtStart:
                continue
                
            t.append( T(float(day['imperial']['tempAvg'])) )        # F -> C
            h.append( float(day['humidityAvg']) )                   # %
            w.append( u2(float(day['imperial']['windspeedAvg'])) )  # MPH -> m/s
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
    pmLogger.debug("Temperature: %.1f to %.1f C", Tmin, Tmax)
    pmLogger.debug("Relative humidity: %.0f%% to %.0f%%", RHmin, RHmax)
    pmLogger.debug("Average wind speed: %.1f m/s",  w)
    pmLogger.debug("Elevation above sea level: %.1f m",  elev)
    pmLogger.debug("Total rainfall: %.2f mm", sum(p))
    pmLogger.debug("Average solar radiation: %.1f W/m^2/d", r)
        
    # Compute the evapotranspiration loss...
    loss = ET(Tmin, Tmax, w, RHmin, RHmax, lat, elev, dtStart, R=r)
    pmLogger.info("ET loss: %.2f mm", loss)
    # ... and correct for the amount of rainfall received.
    loss -= sum(p)
    loss = max([0.0, loss])
    pmLogger.info("ET loss, less rainfall received: %.2f mm", loss)
        
    # Convert, if needed, and return
    if inches:
        loss = loss / 25.4
    return loss
