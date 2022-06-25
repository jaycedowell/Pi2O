# -*- coding: utf-8 -*

"""
Module for controlling a particular sprinkler zone.
"""

import time
import logging
import threading
import traceback
 
from weather import get_current_conditions

__version__ = '0.2'
__all__ = ['GPIORelay', 'GPIORainSensor', 'NullRainSensor', 'SoftRainSensor',
           'SprinklerZone']


# Logger instance
zoneLogger = logging.getLogger('__main__')


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
                with open('/sys/class/gpio/export', 'w') as fh:
                    fh.write(str(self.pin))
                    fh.flush()
                    
                # Direction
                with open('/sys/class/gpio/gpio%i/direction' % self.pin, 'w') as fh:
                    fh.write('out')
                    fh.flush()
                    
                # Off
                self.off()
                
            except IOError:
                pass
                
    def on(self):
        """
        Turn the relay on.
        """
        
        if self.pin > 0:
            with open('/sys/class/gpio/gpio%i/value' % self.pin, 'w') as fh:
                fh.write('1')
                fh.flush()
                
    def off(self):
        """
        Turn the relay off.
        """
    
        if self.pin > 0:
            with open('/sys/class/gpio/gpio%i/value' % self.pin, 'w') as fh:
                fh.write('0')
                fh.flush()


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
                with open('/sys/class/gpio/export', 'w') as fh: 
                    fh.write(str(self.pin))
                    fh.flush()
                    
                # Direction
                with open('/sys/class/gpio/gpio%i/direction' % self.pin, 'w') as fh:
                    fh.write('in')
                    fh.flush()
                    
            except IOError:
                pass
                
    def read(self):
        """
        Read the state of the GPIO.
        """
        
        if self.pin > 0:
            with open('/sys/class/gpio/gpio%i/value' % self.pin, 'r') as fh:
                value = int(fh.read(), 10)
                
            return value
        else:
            return -1
            
    @property
    def is_active(self):
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
        
    @property
    def is_active(self):
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
    
    def __init__(self, precip, config, updateInterval=3600):
        """
        Initialize the class with precipitation amount in inches.  Use
        the 'updateInterval' keyword to set the polling interval in
        seconds (default = 3600).
        """
        
        # Precipitation cutoff
        self.precip = float(precip)
        
        # Configuration link
        self.config = config
        
        # Initialize the internal state
        self.rainfall = 0.0
        self.lastPoll = 0.0
        self.updateInterval = float(updateInterval)
        
    def read(self):
        """
        Read the state of the rain sensor.
        """
        
        tNow = time.time()
        if tNow-self.lastPoll >= self.updateInterval:
            # Refresh
            pws = self.config.get('Weather', 'pws')
            try:
                assert(pws != '')
                data = get_current_conditions(pws)
                current = data['observations'][0]
                
                self.rainfall = float(current['imperial']['precipTotal'])
            except (AssertionError, RuntimeError):
                self.rainfall = -1
            self.lastPoll = tNow
            
        if self.rainfall >= self.precip:
            return 1
        else:
            return 0
            
    @property
    def is_active(self):
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
                rain = self.rainSensor.is_active
                
            if not rain:
                self.relay.on()
                zoneLogger.info('Turned on GPIO pin %i', self.relay.pin)
            else:
                zoneLogger.warning('Rain sensor is active, not activating relay')
                
            self.state = 1
            self.lastStart = time.time()
            
    def off(self):
        if self.state == 1:
            self.relay.off()
            zoneLogger.info('Turned off GPIO pin %i', self.relay.pin)
            
            self.state = 0
            self.lastStop = time.time()
            
    @property
    def is_active(self):
        return True if self.state else False
        
    def get_last_run(self):
        return self.lastStart
