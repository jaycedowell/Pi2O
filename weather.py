# -*- coding: utf-8 -*

"""
Module for reading in weather conditions from Weather Underground.
"""

import json
import time
import logging
import urllib2
from datetime import datetime, timedelta

from expiring_cache import expiring_cache

__version__ = "0.5"
__all__ = ["getCurrentConditions", "getThreeDayHistory", "getCurrentTemperature", 
           "getWeatherAdjustment", "__version__", "__all__"]


# Logger instance
wxLogger = logging.getLogger('__main__')


# Rate limiter
class _rateLimiter(object):
    """
    Class to help make sure that the various calls to the WUnderground API don't exceed 
    the free rate limit of 5 queries per minute.  This class can also be adjusted to 
    work with other rate limits as well.
    """
    
    def __init__(self, requestsPerMinute=5):
        self.requestsPerMinute = float(requestsPerMinute)
        
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
        if requestCount < self.requestsPerMinute:
            ## Yes, update the requests list
            self._requests.append( tNow )
            clear = True
        else:
            if block:
                ## Sleep for a bit and try again
                wxLogger.warning('WUnderground rate limiter in effect')
                time.sleep(5)
                while not self.clearToSend(block=False):
                    time.sleep(5)
                    
                wxLogger.info('WUnderground rate limiter cleared')
                clear = True
            else:
                ## Nope
                clear = False
                
        return clear


# Create the rate limiter
_rl = _rateLimiter()


def getCurrentConditions(pws, timeout=30):
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


def getThreeDayHistory(pws, timeout=30):
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
def getCurrentTemperature(pws, timeout=30):
    """
    Get the current temperature in degrees Fahrenheit using the WUnderground 
    API.
    """
    
    data = getCurrentConditions(pws, timeout=timeout)
    
    try:
        current = data['observations'][0]
        
        tNow = float(current['imperial']['temp'])
        wxLogger.debug('Current temperature is %.1f F for %s', tNow, pws)
    except Exception as e:
        raise RuntimeError("Failed to get current temperature: %s" % str(e))
        
    return tNow


@expiring_cache(maxage=1800)
def getWeatherAdjustment(pws, adj_min=0.0, adj_max=200.0, timeout=30):
    """
    Compute a watering time scale factor using the WUnderground conditions.
    """
    
    # Pre-process
    adj_min = float(adj_min)
    adj_max = float(adj_max)
    
    # Past 24 hours
    dtNow = datetime.utcnow()
    dtStart = dtNow - timedelta(days=1)
    data = getThreeDayHistory(pws, timeout=timeout)
    
    a, t, h, w, p = [], [], [], [], []
    try:
        history = data['observations']
        
        for day in history:
            dt = datetime.utcfromtimestamp(day['epoch'])
            if dt < dtStart:
                continue
                
            a.append( (dtNow - dt).total_seconds() )
            t.append( float(day['imperial']['tempAvg']) )
            h.append( float(day['humidityAvg']) )
            w.append( float(day['imperial']['windspeedAvg']) )
            p.append( float(day['imperial']['precipTotal']) )
            
        ## Convert the total rainfall to Delta_{rain}
        dp = [0.0,]
        for i in xrange(1, len(p)):
            dp.append( p[i]-p[i-1] )
            if dp[-1] < 0:
                dp[-1] = 0.0
        p = dp
        
    except Exception as e:
        wxLogger.warning('Error parsing three-day history: %s', str(e))
        
    # The various bits of the scaling relation
    tFactor = 0.0
    if len(t) != 0:
        tFactor = 4.0*(sum(t)/len(t) - 70.0)    # +4% for every degree above 70 F
    hFactor = 0.0
    if len(h) != 0:
        hFactor = -1.0*(sum(h)/len(h) - 30.0)   # -1% for every percent above 30% RH 
    wFactor = 0.0
    if len(w) != 0:
        wFactor = 2.0*(sum(w)/len(w) - 5.0)     # +2% for every MPH over 5 MPH
        wFactor = max([0.0, wFactor])           # force to be positive only
    pFactor = 0.0
    if len(p) != 0:
        pFactor = -2.0*sum(p)*25                # -2% for every 0.04" of rain
    factor = 100.0 + tFactor + hFactor + wFactor + pFactor
    factor = min([factor, adj_max])
    factor = max([adj_min, factor])
    
    wxLogger.debug('Current weather adjustment is %.1f%% for %s', factor, pws)
    wxLogger.debug('  Corrections')
    wxLogger.debug('    Temperature: %.2f%%', tFactor)
    wxLogger.debug('    Humidity: %.2f%%', hFactor)
    wxLogger.debug('    Wind: %.2f%%', wFactor)
    wxLogger.debug('    Rain: %.2f%%', pFactor)
    
    return factor/100.0
