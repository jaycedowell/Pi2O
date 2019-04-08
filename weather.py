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

__version__ = '0.5'
__all__ = ['get_current_conditions', 'get_three_day_history', 'get_current_temperature', 
           '__version__']


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
