"""
Module for reading a rain sensor hooked into the GPIO ports on a Rasberry Pi.
"""

__version__ = '0.1'
__all__ = ['GPIORainSensor', '__version__', '__all__']


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
			 