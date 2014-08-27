# -*- coding: utf-8 -*

"""
Module for controlling relays hooked into the GPIO ports on a Rasberry Pi.
"""

__version__ = '0.1'
__all__ = ['GPIORelay', '__version__', '__all__']


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
			