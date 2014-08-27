# -*- coding: utf-8 -*

"""
Module for controlling a particular sprinkler zone and setting the schedule.
"""

import time

__version__ = '0.1'
__all__ = ['SprinklerZone', '__version__', '__all__']


class SprinklerZone(object):
	"""
	Class for controlling a particular sprinkler zone.
	"""
	
	def __init__(self, relay, rainSensor=None):
		self.relay = relay
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