#!/usr/bin/env python
# -*- coding: utf-8 -*

import os
import sys
import time
import threading
from datetime import datetime, timedelta

import jinja2
import cherrypy
from cherrypy.process.plugins import Daemonizer

from config import *
from database import Archive
from weather import getWeatherAdjustment

# Daemonize
d = Daemonizer(cherrypy.engine, stderr='/tmp/Pi2O.stderr')
d.subscribe()

# Load in the configuration
config = loadConfig(CONFIG_FILE)

# Initialize the archive
history = Archive()

# Initialize the hardware
hardwareZones = initZones(config)
for previousRun in history.getData():
	if hardwareZones[previousRun['zone']-1].lastStart == 0:
		hardwareZones[previousRun['zone']-1].lastStart = previousRun['dateTimeStart']
		hardwareZones[previousRun['zone']-1].lastStop = previousRun['dateTimeStop']


class ScheduleProcessor(threading.Thread):
	"""
	Class responsible to running the various zones according to the schedule.
	"""

	def __init__(self, interval, bus=None):
		threading.Thread.__init__(self)
		self.interval = interval
		self.running = False
		self.bus = bus

	def cancel(self):
		self.running = False

	def run(self):
		self.running = True
		self.wxAdjust = None
		self.blockActive = False
		
		while self.running:
			time.sleep(self.interval)
			if not self.running:
				return True
				
			try:
				tNow = datetime.now()
				tNow = tNow.replace(second=0, microsecond=0)
				
				# Is the current schedule active?
				if config.get('Schedule%i' % tNow.month, 'enabled') == 'on':
					## If so, query the run interval and start time for this block
					interval = int(config.get('Schedule%i' % tNow.month, 'interval'))
					h,m = [int(i) for i in config.get('Schedule%i' % tNow.month, 'start').split(':', 1)]
					
					## Figure out if it is the start time or if we are inside a schedule 
					## block.  If so, we need to turn things on.
					tSchedule = tNow.replace(hour=int(h), minute=int(m))
					if tSchedule == tNow or self.blockActive:
						### Load in the current weather adjustment, if needed
						if self.wxAdjust is None:
							key = config.get('Weather', 'key')
							pws = config.get('Weather', 'pws')
							pos = config.get('Weather', 'postal')
							if key != '' and pws != '' and pos != '':
								self.wxAdjust = getWeatherAdjustment(key, pws=pws, postal=pos)
								if self.bus is not None:
									self.bus.log('Setting weather adjustment factor to %.3f' % self.wxAdjust)
							else:
								self.wxAdjust = 1.0
								
						### Convert the interval into a timedeltas
						interval = timedelta(days=interval)
						
						### Loop over the zones
						for zone in (1, 2, 3, 4):
							#### What duration do we use for this zone?
							duration = int(config.get('Schedule%i' % tNow.month, 'duration%i' % zone))
							duration = duration*self.wxAdjust
							duration = timedelta(minutes=int(duration), seconds=int((duration*60) % 60))
							
							#### What is the last run time for this zone?
							tLast = datetime.fromtimestamp( hardwareZones[zone-1].getLastRun() )
							
							if hardwareZones[zone-1].isActive():
								#### If the zone is active, check how long it has been on
								if tLast-tNow >= duration:
									hardwareZones[zone-1].off()
									history.writeData(time.time(), zone, 'off')
									if self.bus is not None:
										self.bus.log('Zone %i - off' % zone)
								else:
									self.blockActive = True
									
							else:
								#### Otherwise, is it time to turn it on
								if tSchedule - tLast >= interval:
									hardwareZones[zone-1].on()
									history.writeData(time.time(), zone, 'on', wxAdjustment=self.wxAdjust)
									if self.bus is not None:
										self.bus.log('Zone %i - on' % zone)
									self.blockActive = True
									break
									
							#### If this is the last zone to process and it is off, we
							#### are done with this block
							if zone == 4 and not hardwareZones[zone-1].isActive():
								self.blockActive = False
						
									
				else:
					self.wxAdjust = None
					
			except Exception:
				if self.bus is not None:
					self.bus.log("Error in background task thread function.", level=40, traceback=True)
					
	def _set_daemon(self):
		return True


# Jinja configuration
jinjaEnv = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(os.path.dirname(__file__),'templates')))


# Main web interface
class Interface(object):
	@cherrypy.expose
	def index(self):
		kwds = config.asDict()
		kwds['tNow'] = datetime.now()
		kwds['tzOffset'] = int(datetime.now().strftime("%s")) - int(datetime.utcnow().strftime("%s"))
		for i,zone in enumerate(hardwareZones):
			i += 1
			kwds['zone%i-status' % i] = 'on' if zone.isActive() else 'off'
		for entry in history.getData():
			try:
				kwds['zone%i-lastStart' % entry['zone']]
				kwds['zone%i-lastStop' % entry['zone']]
			except KeyError:
				kwds['zone%i-lastStart' % entry['zone']] = datetime.fromtimestamp(entry['dateTimeStart'])
				kwds['zone%i-lastStop' % entry['zone']] = datetime.fromtimestamp(entry['dateTimeStop'])
				
		template = jinjaEnv.get_template('index.html')
		return template.render({'kwds':kwds})
		
	@cherrypy.expose
	def zones(self, **kwds):
		if len(kwds) == 0:
			kwds = config.asDict()
		else:
			config.fromDict(kwds)
			saveConfig(CONFIG_FILE, config)
			
		template = jinjaEnv.get_template('zones.html')
		return template.render({'kwds':kwds})
	
	@cherrypy.expose
	def schedules(self, **kwds):
		if len(kwds) == 0:
			kwds = config.asDict()
		else:
			config.fromDict(kwds)
			saveConfig(CONFIG_FILE, config)
			
		mname = {1:'January', 2:'February', 3:'March', 4:'April', 5:'May', 6:'June', 
				 7:'July', 8:'August', 9:'September', 10:'October', 11:'November', 12:'December'}
				 
		template = jinjaEnv.get_template('schedules.html')
		return template.render({'kwds':kwds, 'mname':mname})
	
	@cherrypy.expose
	def weather(self, **kwds):
		if len(kwds) == 0:
			kwds = config.asDict()
		else:
			config.fromDict(kwds)
			saveConfig(CONFIG_FILE, config)
			
		if 'test-config' in kwds.keys():
			if kwds['weather-key'] == '':
				kwds['weather-info'] = 'Error: No API key provided'
			elif kwds['weather-pws'] == '' and kwds['weather-postal'] == '':
				kwds['weather-info'] = 'Error: No PWS or postal code provided'
			else:
				kwds['weather-info'] = 'Current weather correction: %i%%' % (100.0*getWeatherAdjustment(kwds['weather-key'], pws=kwds['weather-pws'], postal=kwds['weather-postal']),)
				
		else:
			kwds['weather-info'] = ''
			
		print "Finally"
		template = jinjaEnv.get_template('weather.html')
		return template.render({'kwds':kwds})
	
	@cherrypy.expose
	def manual(self, **kwds):
		if len(kwds) == 0:
			kwds = config.asDict()
		else:
			configDict = config.asDict()
			for keyword,value in configDict.iteritems():
				if keyword not in kwds.keys():
					kwds[keyword] = value
					
		for keyword,value in kwds.iteritems():
			if keyword[:4] == 'zone' and keyword.find('-') == -1:
				i = int(keyword[4:])
				if value == 'on' and not hardwareZones[i-1].isActive():
					hardwareZones[i-1].on()
					history.writeData(time.time(), i, 'on', wxAdjustment=-1.0)
				if value == 'off' and hardwareZones[i-1].isActive():
					hardwareZones[i-1].off()
					history.writeData(time.time(), i, 'off')
					
		kwds['manual-info'] = ''
		for i,zone in enumerate(hardwareZones):
			i = i + 1
			if kwds['zone%i-enabled' % i] == 'on':
				if zone.isActive():
					kwds['zone%i' % i] = 'selected'
					kwds['manual-info'] += 'Zone %i is turned on<br />' % i
				else:
					kwds['zone%i' % i] = ''
					kwds['manual-info'] += 'Zone %i is turned off<br />' % i
				
		template = jinjaEnv.get_template('manual.html')
		return template.render({'kwds':kwds})
		
	@cherrypy.expose
	def logs(self, **kwds):
		kwds = {}
		kwds['tNow'] = datetime.now()
		kwds['tzOffset'] = int(datetime.now().strftime("%s")) - int(datetime.utcnow().strftime("%s"))
		kwds['history'] = history.getData(age=7*24*3600)[:25]
		for i in xrange(len(kwds['history'])):
			kwds['history'][i]['dateTimeStart'] = datetime.fromtimestamp(kwds['history'][i]['dateTimeStart'])
			kwds['history'][i]['dateTimeStop'] = datetime.fromtimestamp(kwds['history'][i]['dateTimeStop'])
			
		template = jinjaEnv.get_template('log.html')
		return template.render({'kwds':kwds})


if __name__ == "__main__":
	cpConfig = {'/css': {'tools.staticdir.on': True,
						 'tools.staticdir.dir': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'css')},
          		'/js':  {'tools.staticdir.on': True,
          				 'tools.staticdir.dir': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'js')}
          		}
				
	bg = ScheduleProcessor(60, bus=cherrypy.engine)
	bg.start()
	
	cherrypy.quickstart(Interface(), config=cpConfig)
	bg.cancel()
	Archive.cancel()
	
	for zone in hardwareZones:
		if zone.isActive():
			zone.off()
			history.writeData(time.time(), i, 'off')
