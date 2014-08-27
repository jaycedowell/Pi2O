"""
Module for reading in weather conditions from Weather Underground using their API.
"""

import json
import urllib

__version__ = "0.1"
__all__ = ["getCurrentConditions", "getYesterdaysConditions", "__version__", "__all__"]


# Base URL for all queries
_baseURL = "http://api.wunderground.com/api"


def getCurrentConditions(apiKey, pws=None, postal=None):
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
		
	uh = urllib.urlopen(url)
	data = json.loads(uh.read())
	uh.close()
	
	return data


def getYesterdaysConditions(apiKey, pws=None, postal=None):
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
		
	uh = urllib.urlopen(url)
	data = json.loads(uh.read())
	uh.close()
	
	return data


def getWeatherAdjustment(apiKey, pws=None, postal=None):
	"""
	Compute a watering time scale factor using the WUnderground conditions.
	"""
	
	cNow = getCurrentConditions(apiKey, pws=pws, postal=postal)
	cPast = getYesterdaysConditions(apiKey, pws=pws, postal=postal)
	
	tNow = float(cNow['current_observation']['temp_f'])
	hNow = float(cNow['current_observation']['relative_humidity'].replace('%', ''))
	pNow = float(cNow['current_observation']['precip_today_in'])
	
	tPast = 0.0
	hPast = 0.0
	pPast = 0.0
	pCount = 0
	for obs in cPast['history']['observations']:
		tPast += float(obs['tempi'])
		hPast += float(obs['hum'])
		pPast += float(obs['precipi']) if float(obs['precipi']) > 0.0 else 0.0
		pCount += 1
	if pCount > 0:
		tPast /= pCount
		hPast /= pCount
		
	tFactor = 4.0*((0.5*tNow + 0.5*tPast) - 70.0)	# percent
	rFactor = 1.0*(30.0 - (0.5*hNow + 0.5*hPast))	# percent
	pFactor = -2.0*((pNow + pPast)*100.0)			# percent
	factor = 100.0 + tFactor + rFactor + pFactor
	factor = min([factor, 200])
	factor = max([0, factor])
	
	return factor/100.0