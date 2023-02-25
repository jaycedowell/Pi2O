# -*- coding: utf-8 -*-

"""
File for dealing with the configuration of Pi2O.py.
"""

import os
import logging
import threading
from configparser import SafeConfigParser, NoSectionError

from zone import GPIORelay, SprinklerZone

__version__ = '0.5'
__all__ = ['CONFIG_FILE', 'LockingConfigParser', 'load_config', 'init_zones',
           'save_config']


# Logger instance
_LOGGER = logging.getLogger('__main__')


# Maximum number of zones to configure
MAX_ZONES = 6


# Files
## Base path for the various files needed/generated by Pi2O.py
_BASE_PATH = os.path.dirname(os.path.abspath(__file__))

## Configuration
CONFIG_FILE = os.path.join(_BASE_PATH, 'Pi2O.config')


class LockingConfigParser(SafeConfigParser):
    """
    Sub-class of ConfigParser.SafeConfigParser that wraps the get, set, and 
    write methods with a semaphore to ensure that only one get/set/read/write 
    happens at a time.  The sub-class also adds a 'dict' property that makes
    it easier to tie the configuration into webforms.
    """
    
    _lock = threading.RLock()
    
    def get(self, *args, **kwds):
        """
        Locked get() method.
        """
        
        with self._lock:
            value = SafeConfigParser.get(self, *args, **kwds)
        return value
        
    def get_aslist(self, *args, **kwds):
        """
        Locked wrapper around get() that interperates the value as a comma-
        separated list of values.
        """
        
        with self._lock:
            value = self.get(*args, **kwds)
            
            output = []
            for v in value.split(','):
                try:
                    v = int(v, 10)
                except ValueError:
                    try:
                        v = float(v)
                    except ValueError:
                        pass
                output.append(v)
        return output
        
    def getint(self, *args, **kwds):
        """
        Locked getint() method.
        """
        
        with self._lock:
            value = SafeConfigParser.getint(self, *args, **kwds)
        return value
        
    def getfloat(self, *args, **kwds):
        """
        Locked getfloat() method.
        """
        
        with self._lock:
            value = SafeConfigParser.getfloat(self, *args, **kwds)
        return value
        
    def set(self, *args, **kwds):
        """
        Locked set() method.
        """
        
        with self._lock:
            SafeConfigParser.set(self, *args, **kwds)
            
    def read(self, *args, **kwds):
        """
        Locked read() method.
        """
        
        with self._lock:
            SafeConfigParser.read(self, *args, **kwds)
            
    def write(self, *args, **kwds):
        """
        Locked write() method.
        """
        
        with self._lock:
            SafeConfigParser.write(self, *args, **kwds)
        
    @property
    def dict(self):
        """
        Return the configuration as a dictionary with keys structured as
        section-option.
        """
        
        with self._lock:
            configDict = {}
            for section in self.sections():
                for keyword,value in self.items(section):
                    configDict['%s-%s' % (section.lower(), keyword.replace('_', '-'))] = value
                    
        # Done
        return configDict
        
    @dict.setter
    def dict(self, configDict):
        """
        Given a dictionary returned by the dict attribute, update the
        configuration as needed.
        """
        
        # Loop over the pairs in the dictionary
        assert(isinstance(configDict, dict))
        with self._lock:
            for key,value in configDict.items():
                try:
                    section, keyword = key.split('-', 1)
                    keyword = keyword.replace('-', '_')
                    section = section.capitalize()
                    if section == 'Rainsensor':
                        section = 'RainSensor'
                    self.set(section, keyword, value)
                except Exception as e:
                    _LOGGER.warning("from_dict with key='%s', value='%s': %s", key, value, str(e))


def load_config(filename):
    """
    Read in the configuration file and return a LockingConfigParser instance.
    """
    
    # Initial configuration file
    config = LockingConfigParser()
    
    ## Dummy information about the four zones:
    ##  1) name - zone nickname
    ##  2) pin - RPi GPIO pin
    ##  3) enabled - whether or not the zone is active
    for zone in range(1, MAX_ZONES+1):
        config.add_section(f"Zone{zone}")
        for keyword in ('name', 'pin', 'rate', 'enabled', 'current_et_value'):
            config.set(f"Zone{zone}", keyword, '')
            if keyword == 'enabled':
                config.set(f"Zone{zone}", keyword, 'off')
            elif keyword in ('rate', 'current_et_value'):
                config.set(f"Zone{zone}", keyword, '0.0')
                
    ## Dummy schedule information - one for each month
    ##  1) start - start time as HH:MM, 24-hour format
    ##  2) threshold - accumulated ET threshold before watering
    ##  3) enabled - whether or not the schedule is active
    ##  4) zones_to_skip - list of zones to skip
    for month in range(1, 13):
        config.add_section(f"Schedule{month}")
        for keyword in ('start', 'threshold', 'enabled'):
            if keyword == 'threshold':
                config.set(f"Schedule{month}", keyword, '0.5')
            elif keyword == 'enabled':
                config.set(f"Schedule{month}", keyword, 'off')
            elif keyword == 'zones_to_skip':
                config.set(f"Schedule{month}", keyword, '')
            else:
                config.set(f"Schedule{month}", keyword, '')
                
    ## Dummy schedule limiter
    ##  1) limiter - whehter or not the limiter is active
    ##  2) max_zones - maximum number of zones to run on a given day
    config.add_section('Schedule')
    config.set('Schedule', 'limiter', 'off')
    config.set('Schedule', 'max_zones', '0')
    
    ## Dummy weather station information
    ##  1) pws - PWS ID to use for weather info
    ##  2) kc - Crop constant
    ##  3) cn - Crop type numerator constant
    ##  4) cd - Crop type denominator constant
    config.add_section('Weather')
    for keyword in ('pws', 'kc', 'cn', 'cd'):
        if keyword == 'kc':
            config.set('Weather', 'kc', '1.0')
        elif keyword == 'cn':
            config.set('Weather', 'cn', '900.0')
        elif keyword == 'cd':
            config.set('Weather', 'cd', '0.34')
        else:
            config.set('Weather', keyword, '')
            
    # Try to read in the actual configuration file
    try:
        config.read(filename)
        _LOGGER.info('Loaded configuration from \'%s\'', os.path.basename(filename))
        
    except:
        pass
        
    # Done
    return config


def init_zones(config):
    """
    Given a LockingConfigParser configuration instance, create a list of 
    SprinklerZone instances to control the various zones.
    """
    
    # Create the list of SprinklerZone instances
    zones = []
    zone = 1
    while True:
        try:
            ## Is the zone enabled?
            zoneEnabled = config.get(f"Zone{zone}", 'enabled')
            if zoneEnabled == 'on':
                ### If so, use the real GPIO pin
                zonePin = config.getint(f"Zone{zone}", 'pin')
            else:
                ### If not, use a dummy pin
                zonePin = -1
                
            ## Create the SprinklerZone instance
            zones.append( SprinklerZone(zonePin, 
                                        rate=config.getfloat(f"Zone{zone}", 'rate'), 
                                        current_et_value=config.getfloat(f"Zone{zone}", 'current_et_value'))
                        )
            
            ## Update the counter
            zone += 1
            
        except NoSectionError:
            break
            
    # Done
    return zones


def save_config(filename, config):
    """
    Given a filename and a LockingConfigParser, write the configuration to 
    disk.
    """
    
    with open(filename, 'w') as fh:
        config.write(fh)
        
    _LOGGER.info(f"Saved configuration to '{os.path.basename(filename)}'")
