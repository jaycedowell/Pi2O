#!/usr/bin/env python

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
from scheduler import ScheduleProcessor
from weather import getWeatherAdjustment


# Jinja configuration
jinjaEnv = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(os.path.dirname(__file__),'templates')))


# Main web interface
class Interface(object):
	def __init__(self, config, hardwareZones, history):
		self.config = config
		self.hardwareZones = hardwareZones
		self.history = history
		
	@cherrypy.expose
	def index(self):
		kwds = self.config.asDict()
		kwds['tNow'] = datetime.now()
		kwds['tzOffset'] = int(datetime.now().strftime("%s")) - int(datetime.utcnow().strftime("%s"))
		for i,zone in enumerate(self.hardwareZones):
			i += 1
			kwds['zone%i-status' % i] = 'on' if zone.isActive() else 'off'
		for entry in self.history.getData():
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
			kwds = self.config.asDict()
		else:
			self.config.fromDict(kwds)
			saveConfig(CONFIG_FILE, self.config)
			
		template = jinjaEnv.get_template('zones.html')
		return template.render({'kwds':kwds})
	
	@cherrypy.expose
	def schedules(self, **kwds):
		if len(kwds) == 0:
			kwds = self.config.asDict()
		else:
			self.config.fromDict(kwds)
			saveConfig(CONFIG_FILE, self.config)
			
		mname = {1:'January', 2:'February', 3:'March', 4:'April', 5:'May', 6:'June', 
				 7:'July', 8:'August', 9:'September', 10:'October', 11:'November', 12:'December'}
				 
		template = jinjaEnv.get_template('schedules.html')
		return template.render({'kwds':kwds, 'mname':mname})
	
	@cherrypy.expose
	def weather(self, **kwds):
		if len(kwds) == 0:
			kwds = self.config.asDict()
		else:
			self.config.fromDict(kwds)
			saveConfig(CONFIG_FILE, self.config)
			
		if 'test-config' in kwds.keys():
			if kwds['weather-key'] == '':
				kwds['weather-info'] = 'Error: No API key provided'
			elif kwds['weather-pws'] == '' and kwds['weather-postal'] == '':
				kwds['weather-info'] = 'Error: No PWS or postal code provided'
			else:
				kwds['weather-info'] = 'Current weather correction: %i%%' % (100.0*getWeatherAdjustment(kwds['weather-key'], pws=kwds['weather-pws'], postal=kwds['weather-postal']),)
				
		else:
			kwds['weather-info'] = ''
			
		template = jinjaEnv.get_template('weather.html')
		return template.render({'kwds':kwds})
	
	@cherrypy.expose
	def manual(self, **kwds):
		if len(kwds) == 0:
			kwds = self.config.asDict()
		else:
			self.configDict = self.config.asDict()
			for keyword,value in configDict.iteritems():
				if keyword not in kwds.keys():
					kwds[keyword] = value
					
		for keyword,value in kwds.iteritems():
			if keyword[:4] == 'zone' and keyword.find('-') == -1:
				i = int(keyword[4:])
				if value == 'on' and not self.hardwareZones[i-1].isActive():
					self.hardwareZones[i-1].on()
					self.history.writeData(time.time(), i, 'on', wxAdjustment=-1.0)
				if value == 'off' and self.hardwareZones[i-1].isActive():
					self.hardwareZones[i-1].off()
					self.history.writeData(time.time(), i, 'off')
					
		kwds['manual-info'] = ''
		for i,zone in enumerate(self.hardwareZones):
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
		kwds['history'] = self.history.getData(age=7*24*3600)[:25]
		for i in xrange(len(kwds['history'])):
			kwds['history'][i]['dateTimeStart'] = datetime.fromtimestamp(kwds['history'][i]['dateTimeStart'])
			kwds['history'][i]['dateTimeStop'] = datetime.fromtimestamp(kwds['history'][i]['dateTimeStop'])
			
		template = jinjaEnv.get_template('log.html')
		return template.render({'kwds':kwds})


if __name__ == "__main__":
	# CherryPy configuration
	cherrypy.config.update({'server.socket_host': '0.0.0.0',})#'environment': 'production'})
	cpConfig = {'/css': {'tools.staticdir.on': True,
						 'tools.staticdir.dir': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'css')},
          		'/js':  {'tools.staticdir.on': True,
          				 'tools.staticdir.dir': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'js')}
          		}
				
	## Daemonize
	#d = Daemonizer(cherrypy.engine, stdout='/tmp/Pi2O.stdout', stderr='/tmp/Pi2O.stderr')
	#d.subscribe()
	
	# Load in the configuration
	config = loadConfig(CONFIG_FILE)
	
	# Initialize the archive
	history = Archive(config, bus=cherrypy.engine)
	history.start()
	
	# Initialize the hardware
	hardwareZones = initZones(config)
	for previousRun in history.getData():
		if hardwareZones[previousRun['zone']-1].lastStart == 0:
			hardwareZones[previousRun['zone']-1].lastStart = previousRun['dateTimeStart']
			hardwareZones[previousRun['zone']-1].lastStop = previousRun['dateTimeStop']
			
	# Initialize the scheduler
	bg = ScheduleProcessor(config, hardwareZones, history, bus=cherrypy.engine)
	bg.start()
	
	# Initialize the web interface
	ws = Interface(config, hardwareZones, history)
	#cherrypy.quickstart(ws, config=cpConfig)
	cherrypy.tree.mount(ws, "/", config=cpConfig)
	cherrypy.engine.start()
	cherrypy.engine.block()
	
	# Stop the scheduler thread
	bg.cancel()
	
	# Make sure the sprinkler zones are off
	for zone in hardwareZones:
		if zone.isActive():
			zone.off()
			history.writeData(time.time(), i, 'off')
			
	# Shutdown the archive
	history.cancel()
	
	# Save the final configuration
	saveConfig(CONFIG_FILE, config)
	
	
