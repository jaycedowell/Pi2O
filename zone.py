# -*- coding: utf-8 -*

"""
Module for controlling a particular sprinkler zone.
"""

import time

__version__ = '0.1'
__all__ = ['GPIORelay', 'GPIORainSensor', 'NullRainSensor', 'SoftRainSensor', 'SprinklerZone', '__version__', '__all__']


class GPIORelay(object):
	"""
	Class for controlling a relay that is active when high via GPIO.
	"""
	
	def __init__(self, pin):
		"""
		Initialize the class with a GPIO pin number and make sure that the 
		specified as been exported via /sys/class/gpio/export and that it 
		is marked for output.
		"""
	
		# GPIO pin
		self.pin = int(pin)
		
		# Setup
		if self.pin > 0:
			try:	
				# Export
				fh = open('/sys/class/gpio/export', 'w')
				fh.write(str(self.pin))
				fh.flush()
				fh.close()
				
				# Direction
				fh = open('/sys/class/gpio/gpio%i/direction' % self.pin, 'w')
				fh.write('out')
				fh.flush()
				fh.close()
				
				# Off
				self.off()
				
			except IOError:
				pass
				
	def on(self):
		"""
		Turn the relay on.
		"""
		
		if self.pin > 0:
			pass
			#fh = open('/sys/class/gpio/gpio%i/value' % self.pin, 'w')
			#fh.write('1')
			#fh.close()
			
	def off(self):
		"""
		Turn the relay off.
		"""
	
		if self.pin > 0:
			pass
			#fh = open('/sys/class/gpio/gpio%i/value' % self.pin, 'w')
			#fh.write('0')
			#fh.close()


class GPIORainSensor(object):
	"""
	Class for reading a rain sensor via GPIO.  When rain is detected the pin 
	will go high.
	"""
	
	def __init__(self, pin):
		"""
		Initialize the class with a GPIO pin number and make sure that the 
		specified as been exported via /sys/class/gpio/export and that it 
		is marked for input.
		"""
	
		# GPIO pin
		self.pin = int(pin)
		
		# Setup
		if self.pin > 0:
			try:	
				# Export
				fh = open('/sys/class/gpio/export', 'w')
				fh.write(str(self.pin))
				fh.flush()
				fh.close()
				
				# Direction
				fh = open('/sys/class/gpio/gpio%i/direction' % self.pin, 'w')
				fh.write('in')
				fh.flush()
				fh.close()
				
			except IOError:
				pass
				
	def read(self):
		"""
		Read the state of the GPIO.
		"""
		
		if self.pin > 0:
			fh = open('/sys/class/gpio/gpio%i/value' % self.pin, 'r')
			value = int(fh.read(), 10)
			fh.close()
			
			return value
		else:
			return -1
			
	def isActive(self):
		"""
		Read the sensor and return a boolean of whether or not the sensor 
		is active.  Is it raining or not?
		"""
		
		value = self.read()
		if value > 0:
			return True
		else:
			return False


class NullRainSensor(object):
	"""
	Class implementing a null rain sensor (it's always off).
	"""
	
	def __init__(self):
		"""
		Initialize the class.
		"""
		
		pass
				
	def read(self):
		"""
		Read the state which is always off.
		"""
		
		return 0
			
	def isActive(self):
		"""
		Read the sensor and return a boolean of whether or not the sensor 
		is active.  Is it raining or not?
		"""
		
		value = self.read()
		if value > 0:
			return True
		else:
			return False


class SoftRainSensor(object):
	"""
	Class for using weather station data as a software rain sensor.
	"""
	
	def __init__(self, precip, updateInterval=3600):
		"""
		Initialize the class with precipitation amount in inches.  Use
		the 'updateInterval' keyword to set the polling interval in
		seconds (default = 3600).
		"""
	
		# Precipitation cutoff
		self.precip = float(precip)
		
		# Update interval state
		self.lastPoll = 0.0
		self.updateInterval = float(updateInterval)
		
	def read(self):
		"""
		Read the state of the rain sensor.
		"""
		
		tNow = time.time()
		if tNow-self.lastPoll >= self.updateInterval:
			# Refresh
			self.lastPoll = tNow
			
		rain = 0.0
		if rain >= self.precip:
			return 1
		else:
			return 0
			
	def isActive(self):
		"""
		Read the sensor and return a boolean of whether or not the sensor 
		is active.  Is it raining or not?
		"""
		
		value = self.read()
		if value > 0:
			return True
		else:
			return False


class SprinklerZone(object):
	"""
	Class for controlling a particular sprinkler zone.
	"""
	
	def __init__(self, relay, rainSensor=None):
		if type(relay) == GPIORelay:
			self.relay = relay
		else:
			self.relay = GPIORelay(relay)
		self.relay.off()
		self.rainSensor = rainSensor
		
		self.state = 0
		self.lastStart = 0
		self.lastStop = 0
		
	def on(self):
		if self.state == 0:
			rain = False
			if self.rainSensor is not None:
				rain = self.rainSensor.isActive()
				
			if not rain:
				self.relay.on()
			
			self.state = 1
			self.lastStart = time.time()
			
	def off(self):
		if self.state == 1:
			self.relay.off()
			
			self.state = 0
			self.lastStop = time.time()
			
	def isActive(self):
		return True if self.state else False
		
	def getLastRun(self):
		return self.lastStart