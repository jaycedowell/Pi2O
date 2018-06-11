# -*- coding: utf-8 -*

"""
Module for reading in weather conditions from Weather Underground using their API.
"""

import json
import time
import logging
import urllib2
from datetime import datetime, timedelta

from expiring_cache import expiring_cache

__version__ = "0.3"
__all__ = ["getCurrentConditions", "getYesterdaysConditions", "getHistory", "getCurrentTemperature", 
		   "getWeatherAdjustment", "__version__", "__all__"]


# Logger instance
wxLogger = logging.getLogger('__main__')


# Base URL for all queries
_baseURL = "http://api.wunderground.com/api"


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


def getCurrentConditions(apiKey, pws=None, postal=None, timeout=30):
	"""
	Get the current conditions of the personal weather station or postal 
	code using the WUnderground API.
	
	.. note::
		This requires a valid API key to work.
	"""
	
	if pws is not None and pws != '':
		url = "%s/%s/conditions/q/pws:%s.json" % (_baseURL, apiKey, pws)
	elif postal is not None and postal != '':
		url = "%s/%s/conditions/q/%s.json" % (_baseURL, apiKey, str(postal))
	else:
		raise RuntimeError("Must specify either a PWS ID or a postal code")
		
	# Check the rate limiter
	_rl.clearToSend()
	
	try:
		uh = urllib2.urlopen(url, None, timeout)
	except Exception as e:
		raise RuntimeError("Failed to connect to WUnderground: %s" % str(e))
	else:
		data = json.loads(uh.read())
		uh.close()
		if 'error' in data['response']:
			raise RuntimeError("An error occured connecting to WUnderground: %s" % data['response']['error'])
       		
	return data


def getYesterdaysConditions(apiKey, pws=None, postal=None, timeout=30):
	"""
	Get yesterday's conditions of the personal weather station or postal 
	code using the WUnderground API.
	
	.. note::
		This requires a valid API key at the 'Anvil' level to work.
	"""
	
	if pws is not None and pws != '':
		url = "%s/%s/yesterday/q/pws:%s.json" % (_baseURL, apiKey, pws)
	elif postal is not None and postal != '':
		url = "%s/%s/yesterday/q/%s.json" % (_baseURL, apiKey, str(postal))
	else:
		raise RuntimeError("Must specify either a PWS ID or a postal code")
		
	# Check the rate limiter
	_rl.clearToSend()
	
	try:
		uh = urllib2.urlopen(url, None, timeout)
	except Exception as e:
		raise RuntimeError("Failed to connect to WUnderground: %s" % str(e))
	else:
		data = json.loads(uh.read())
		uh.close()
		if 'error' in data['response']:
			raise RuntimeError("An error occured connecting to WUnderground: %s" % data['response']['error'])
       		
	return data


def getHistory(apiKey, date, pws=None, postal=None, timeout=30):
	"""
	Get the weather history for the specified date/YYYYMMDD date string 
	of the personal weather station or postal code using the 
	WUndergroundAPI.
	
	.. note::
		This requires a valid API key with history to work.
	"""
	
	# Process the date
	if type(date) is datetime:
		date = date.strftime("%Y%m%d")
	else:
		try:
			date = datetime.strptime(date, "%Y%m%d")
			date = date.strftime("%Y%m%d")
		except ValueError:
			raise ValueError("date does not appears to be in YYYYMMDD format")
			
	if pws is not None and pws != '':
		url = "%s/%s/history_%s/q/pws:%s.json" % (_baseURL, apiKey, date, pws)
	elif postal is not None and postal != '':
		url = "%s/%s/history_%s/q/%s.json" % (_baseURL, apiKey, date, str(postal))
	else:
		raise RuntimeError("Must specify either a PWS ID or a postal code")
		
	# Check the rate limiter
	_rl.clearToSend()
	
	try:
		uh = urllib2.urlopen(url, None, timeout)
	except Exception as e:
		raise RuntimeError("Failed to connect to WUnderground: %s" % str(e))
	else:
		data = json.loads(uh.read())
		uh.close()
		if 'error' in data['response']:
			raise RuntimeError("An error occured connecting to WUnderground: %s" % data['response']['error'])
       		
	return data


@expiring_cache(maxage=3600)
def getCurrentTemperature(apiKey, pws=None, postal=None, timeout=30):
	"""
	Get the current temperature in degrees Fahrenheit using the WUnderground 
	API.
	"""
	
	cNow = getCurrentConditions(apiKey, pws=pws, postal=postal, timeout=timeout)
	
	tNow = float(cNow['current_observation']['temp_f'])
	wxLogger.debug('Current temperature is %.1f F for %s/%s', tNow, pws, postal)
	
	return tNow


@expiring_cache(maxage=3600)
def getWeatherAdjustment(apiKey, pws=None, postal=None, timeout=30):
	"""
	Compute a watering time scale factor using the WUnderground conditions.
	"""
	
	# Today
	dtNow = datetime.now()
	cNow = getHistory(apiKey, dtNow, pws=pws, postal=postal, timeout=timeout)
	tNow = float(cNow['history']['dailysummary'][0]['meantempi'])
	try:
		hNow = float(cNow['history']['dailysummary'][0]['humidity'])
	except ValueError:
		hNow  = float(cNow['history']['dailysummary'][0]['minhumidity'])
		hNow += float(cNow['history']['dailysummary'][0]['maxhumidity'])
		hNow /= 2.0
	wNow = float(cNow['history']['dailysummary'][0]['meanwindspdi'])
	pNow = float(cNow['history']['dailysummary'][0]['precipi'])
	
	# Yesterday
	dtPast  = dtNow - timedelta(days=1)
	cPast = getHistory(apiKey, dtPast, pws=pws, postal=postal, timeout=timeout)
	tPast = float(cPast['history']['dailysummary'][0]['meantempi'])
	try:
		hPast = float(cPast['history']['dailysummary'][0]['humidity'])
	except ValueError:
		hPast  = float(cPast['history']['dailysummary'][0]['minhumidity'])
		hPast += float(cPast['history']['dailysummary'][0]['maxhumidity'])
		hPast /= 2.0
	wPast = float(cPast['history']['dailysummary'][0]['meanwindspdi'])
	pPast = float(cPast['history']['dailysummary'][0]['precipi'])
	
	# The various bits of the scaling relation
	tFactor =  4.0*((0.5*tNow + 0.5*tPast) - 70.0)	# percent
	rFactor =  1.0*(30.0 - (0.5*hNow + 0.5*hPast))	# percent
	wFactor =  2.0*(0.5*wNow + 0.5*wPast)			# percent
	pFactor = -2.0*((pNow + pPast)*100.0)			# percent
	factor = 100.0 + tFactor + rFactor + wFactor + pFactor
	factor = min([factor, 200])
	factor = max([0, factor])
	
	wxLogger.debug('Current weather adjustment is %.1f%% for %s/%s', factor, pws, postal)
	
	return factor/100.0
