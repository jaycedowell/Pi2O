#!/usr/bin/env python
# -*- coding: utf-8 -*

import os
import sys
import time
import threading
import ConfigParser
from datetime import datetime, timedelta

import jinja2
import cherrypy

import weather
from zone import GPIORelay, GPIORainSensor, NullRainSensor, SoftRainSensor, SprinklerZone

# Initial configuration file
config = ConfigParser.SafeConfigParser()
for zone in (1, 2, 3, 4):
	config.add_section('Zone%i' % zone)
	for keyword in ('name', 'pin', 'enabled', 'last'):
		config.set('Zone%i' % zone, keyword, '')
		if keyword == 'enabled':
			config.set('Zone%i' % zone, keyword, 'off')
config.add_section('RainSensor')
config.set('RainSensor', 'type', 'off')
config.set('RainSensor', 'pin', '')
config.set('RainSensor', 'precip', '')
for month in xrange(1, 13):
	config.add_section('Schedule%i' % month)
	for keyword in ('start', 'duration', 'interval', 'enabled'):
		config.set('Schedule%i' % month, keyword, '')
		if keyword == 'enabled':
			config.set('Schedule%i' % month, keyword, 'off')
config.add_section('Weather')
for keyword in ('key', 'pws', 'postal', 'enabled'):
	config.set('Weather', keyword, '')
	if keyword == 'enabled':
		config.set('Weather', keyword, 'off')
try:
	config.read('Pi2O.config')
except:
	pass


# Initialize the zones based on this file
if config.get('RainSensor', 'type') == 'off':
	rs = NullRainSensor()
elif config.get('RainSensor', 'type') == 'software':
	rs = SoftRainSensor(config.get('RainSensor', 'precip'), config)
else:
	rs = GPIORainSensor( int(config.get('RainSensor', 'pin')) )
	
hardwareZones = []
for zone in (1, 2, 3, 4):
	if config.get('Zone%i' % zone, 'enabled') == 'on':
		pin = int(config.get('Zone%i' % zone, 'pin'))
	else:
		pin = -1
	hardwareZones.append( SprinklerZone(pin, rainSensor=rs) )


# Configure file access semaphore
lock = threading.Semaphore()


class BackgroundTask(threading.Thread):
	"""A subclass of threading.Thread whose run() method repeats.

	Use this class for most repeating tasks. It uses time.sleep() to wait
	for each interval, which isn't very responsive; that is, even if you call
	self.cancel(), you'll have to wait until the sleep() call finishes before
	the thread stops. To compensate, it defaults to being daemonic, which means
	it won't delay stopping the whole process.
	"""

	def __init__(self, interval, bus=None, lock=lock):
		threading.Thread.__init__(self)
		self.interval = interval
		self.running = False
		self.bus = bus
		self.lock = lock

	def cancel(self):
		self.running = False

	def run(self):
		self.running = True
		self.wxAdjust = None
		
		while self.running:
			time.sleep(self.interval)
			if not self.running:
				return True
				
			self.lock.acquire()
			
			try:
				tNow = datetime.now()
				
				# Is the current schedule active?
				if config.get('Schedule%i' % tNow.month, 'enabled') == 'on':
					## If so, query the run interval, duration, and start time
					interval = int(config.get('Schedule%i' % tNow.month, 'interval'))
					duration = int(config.get('Schedule%i' % tNow.month, 'duration'))
					h,m = [int(i) for i in config.get('Schedule%i' % tNow.month, 'start').split(':', 1)]
					
					## Figure out if the start time is in the future.  If not, see what
					## we need to do
					tSchedule = tNow.replace(hour=int(h), minute=int(m))
					if tSchedule <= tNow:
						### Load in the current weather adjustment, if needed
						if self.wxAdjust is None:
							key = config.get('Weather', 'key')
							pws = config.get('Weather', 'pws')
							pos = config.get('Weather', 'postal')
							if key != '' and pws != '' and pos != '':
								self.wxAdjust = weather.getWeatherAdjustment(key, pws=pws, postal=pos)
								if self.bus is not None:
									self.bus.log('Setting weather adjustment factor to %.3f' % self.wxAdjust)
							else:
								self.wxAdjust = 1.0
								
						### Convert the interval and duration value into timedeltas, folding in
						### the weather adjustment factor
						interval = timedelta(days=interval)
						duration = duration*self.wxAdjust
						duration = timedelta(minutes=int(duration), seconds=int((duration*60) % 60))
						
						### Loop over the zones
						zoneOn = False
						for zone in (1, 2, 3, 4):
							#### What is the last run time for this zone?
							tLast = datetime.fromtimestamp( hardwareZones[zone-1].getLastRun() )
							
							if hardwareZones[zone-1].isActive():
								#### If the zone is active, check how long it has been on
								if tLast-tNow >= duration:
									hardwareZones[zone-1].off()
									if self.bus is not None:
										self.bus.log('Zone %i - off' % zone)
								else:
									break
							else:
								#### Otherwise, is it time to turn it on
								if tSchedule - tLast >= interval:
									hardwareZones[zone-1].on()
									if self.bus is not None:
										self.bus.log('Zone %i - on' % zone)
									break
									
				else:
					self.wxAdjust = None
					
			except Exception:
				if self.bus is not None:
					self.bus.log("Error in background task thread function.", level=40, traceback=True)
					
			self.lock.release()

	def _set_daemon(self):
		return True


# Jinja configuration
jinjaEnv = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(os.path.dirname(__file__),'templates')))


# Main web interface
class Interface(object):
	@cherrypy.expose
	def index(self):
		lock.acquire()
		
		kwds = {}
		kwds['tNow'] = datetime.now()
		kwds['tzOffset'] = int(datetime.now().strftime("%s")) - int(datetime.utcnow().strftime("%s"))
		for zone in (1, 2, 3, 4):
			for keyword in ('name', 'pin', 'enabled'):
				kwds['zone%i-%s' % (zone, keyword)] = config.get('Zone%i' % zone, keyword)
				if keyword == 'enabled':
					if kwds['zone%i-%s' % (zone, keyword)] == 'on':
						kwds['zone%i-%s' % (zone, keyword)] = 'enabled'
					else:
						kwds['zone%i-%s' % (zone, keyword)] = 'disabled'
			if hardwareZones[zone-1].isActive():
					kwds['zone%i-status' % zone] = 'on'
			else:
					kwds['zone%i-status' % zone] = 'off'
					
		lock.release()
					
		template = jinjaEnv.get_template('index.html')
		return template.render({'kwds':kwds})
		
	@cherrypy.expose
	def zones(self, **kwds):
		lock.acquire()
		
		configSet = False
		if len(kwds.keys()) > 4:
			for zone in (1, 2, 3, 4):
				try:
					kwds['zone%i-enabled' % zone]
				except KeyError:
					configSet = True
					kwds['zone%i-enabled' % zone] = 'off'
					
		for zone in (1, 2, 3, 4):
			for keyword in ('name', 'pin', 'enabled'):
				try:
					config.set('Zone%i' % zone, keyword, kwds['zone%i-%s' % (zone, keyword)])
					configSet = True
					
					if keyword == 'enabled':
						if kwds['zone%i-%s' % (zone, keyword)] == 'on':
							kwds['zone%i-%s' % (zone, keyword)] = 'selected'
						else:
							kwds['zone%i-%s' % (zone, keyword)] = ''
				except KeyError:
					try:
						kwds['zone%i-%s' % (zone, keyword)] = config.get('Zone%i' % zone, keyword)
						if keyword == 'enabled':
							if kwds['zone%i-%s' % (zone, keyword)] == 'on':
								kwds['zone%i-%s' % (zone, keyword)] = 'selected'
							else:
								kwds['zone%i-%s' % (zone, keyword)] = ''
					except:
						kwds['zone%i-%s' % (zone, keyword)] = ""
		for keyword in ('type', 'pin', 'precip'):
			try:
					config.set('RainSensor', keyword, kwds['rs-%s' % keyword])
					configSet = True
			except KeyError:
				kwds['rs-%s' % keyword] = config.get('RainSensor', keyword)
				
		if configSet:
			fh = open('Pi2O.config', 'w')
			config.write(fh)
			fh.close()
			
		lock.release()
		
		template = jinjaEnv.get_template('zones.html')
		return template.render({'kwds':kwds})
	
	@cherrypy.expose
	def schedules(self, **kwds):
		lock.acquire()
		
		configSet = False
		if len(kwds.keys()) > 4:
			for month in xrange(1, 13):
				try:
					kwds['schedule%i-enabled' % month]
				except KeyError:
					configSet = True
					kwds['schedule%i-enabled' % month] = 'off'
					
		for month in xrange(1, 13):
			for keyword in ('start', 'duration', 'interval', 'enabled'):
				try:
					config.set('Schedule%i' % month, keyword, kwds['schedule%i-%s' % (month, keyword)])
					configSet = True
					
					if keyword == 'enabled':
						if kwds['schedule%i-%s' % (month, keyword)] == 'on':
							kwds['schedule%i-%s' % (month, keyword)] = 'checked'
						else:
							kwds['schedule%i-%s' % (month, keyword)] = ''
				except KeyError:
					try:
						kwds['schedule%i-%s' % (month, keyword)] = config.get('Schedule%i' % month, keyword)
						if keyword == 'enabled':
							if kwds['schedule%i-%s' % (month, keyword)] == 'on':
								kwds['schedule%i-%s' % (month, keyword)] = 'checked'
							else:
								kwds['schedule%i-%s' % (month, keyword)] = ''
					except:
						kwds['schedule%i-%s' % (month, keyword)] = ""
						
		if configSet:
			fh = open('Pi2O.config', 'w')
			config.write(fh)
			fh.close()
			
		lock.release()
		
		mname = {1:'January', 2:'February', 3:'March', 4:'April', 5:'May', 6:'June', 
				 7:'July', 8:'August', 9:'September', 10:'October', 11:'November', 12:'December'}
		
		template = jinjaEnv.get_template('schedules.html')
		return template.render({'kwds':kwds, 'mname':mname})
	
	@cherrypy.expose
	def weather(self, **kwds):
		lock.acquire()	
		
		configSet = False
		for keyword in ('key', 'postal', 'pws', 'enabled'):
			try:
				config.set('Weather', keyword, kwds['weather-%s' % keyword])
				configSet = True
				
				if keyword == 'enabled':
					if kwds['weather-%s' % keyword] == 'on':
						kwds['weather-%s' % keyword] = 'selected'
					else:
						kwds['weather-%s' % keyword] = ''
			except KeyError:
				try:
					kwds['weather-%s' % keyword] = config.get('Weather', keyword)
					if keyword == 'enabled':
						if kwds['weather-%s' % keyword] == 'on':
							kwds['weather-%s' % keyword] = 'selected'
						else:
							kwds['weather-%s' % keyword] = ''
				except:
					kwds['weather-%s' % keyword] = ""
						
		if configSet:
			fh = open('Pi2O.config', 'w')
			config.write(fh)
			fh.close()
			
		lock.release()
		
		if 'weather-test' in kwds.keys():
			if kwds['weather-key'] == '':
				kwds['weather-info'] = 'Error: No API key provided'
			elif kwds['weather-pws'] == '' and kwds['weather-postal'] == '':
				kwds['weather-info'] = 'Error: No PWS or postal code provided'
			else:
				kwds['weather-info'] = 'Current weather correction: %i%%' % (100.0*weather.getWeatherAdjustment(kwds['weather-key'], pws=kwds['weather-pws'], postal=kwds['weather-postal']),)
				
		else:
			kwds['weather-info'] = ''
			
		template = jinjaEnv.get_template('weather.html')
		return template.render({'kwds':kwds})
	
	@cherrypy.expose
	def manual(self, **kwds):
		lock.acquire()
	
		kwds['manual-info'] = ''
		
		if len(kwds.keys()) == 1:
			for zone in (1, 2, 3, 4):
				for keyword in ('name', 'enabled'):
					kwds['zone%i-%s' % (zone, keyword)] = config.get('Zone%i' % zone, keyword)
				kwds['zone%i' % zone] = 'selected' if hardwareZones[zone-1].isActive() else ''
				
		else:
			for zone in (1, 2, 3, 4):
				for keyword in ('name', 'enabled'):
					kwds['zone%i-%s' % (zone, keyword)] = config.get('Zone%i' % zone, keyword)
				try:
					if kwds['zone%i' % zone] == 'on':
						hardwareZones[zone-1].on()
						kwds['zone%i' % zone] = 'selected'
						kwds['manual-info'] += 'Zone %i turned on<br />' % zone
					else:
						hardwareZones[zone-1].off()
						kwds['zone%i' % zone] = ''
						kwds['manual-info'] += 'Zone %i turned off<br />' % zone
						
				except KeyError:
					hardwareZones[zone-1].off()
					kwds['zone%i' % zone] = ''
					kwds['manual-info'] += 'Zone %i turned off<br />' % zone
					
		lock.release()
		
		print [z.isActive() for z in hardwareZones]
		template = jinjaEnv.get_template('manual.html')
		return template.render({'kwds':kwds})
		
	@cherrypy.expose
	def logs(self, **kwds):
		lock.acquire()
		
		lock.release()
		
		return """
<html>
<body>
	<p>Logs</p>
	<br />
	<a href="/">Home</a> &nbsp;|&nbsp;
	<a href="/zones">Zones</a> &nbsp;|&nbsp;
	<a href="/schedules">Schedules</a> &nbsp;|&nbsp;
	<a href="/weather">Weather</a> &nbsp;|&nbsp;
	<a href="/manual">Manual Control</a> &nbsp;|&nbsp;
</body>
</html>"""


if __name__ == "__main__":
	cpConfig = {'/css': {'tools.staticdir.on': True,
						 'tools.staticdir.dir': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'css')},
          		'/js':  {'tools.staticdir.on': True,
          				 'tools.staticdir.dir': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'js')}
          		}
				
	bg = BackgroundTask(60, bus=cherrypy.engine)
	bg.start()
	
	cherrypy.quickstart(Interface(), config=cpConfig)
	bg.cancel()
	
	for zone in hardwareZone:
		zone.off()
		