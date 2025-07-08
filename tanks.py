# -*- coding: utf-8 -*

"""
Module for reading in tank conditions from a RainCache device.
"""

import os
import sys
import pytz
import time
import numpy as np
import logging
import threading
import traceback
import subprocess
from urllib.request import urlopen
from datetime import datetime, timedelta

import matplotlib
matplotlib.use('agg')
from matplotlib import pyplot as plt
import matplotlib.dates as mdates

from expiring_cache import expiring_cache
from database import DatabaseProcessor

__version__ = '0.3'
__all__ = ['MIN_VALID_DEPTH', 'get_current_temperature', 'get_current_depth', 'get_current_volume', 'TankLogger']


MIN_VALID_DEPTH = 0.0    # Inches


# Logger instance
_LOGGER = logging.getLogger('__main__')


@expiring_cache(maxage=300)
def _poll_raincache(ip, timeout=30):
    """
    Poll a rain cache device at the specified IP address and return a seven-
    element tuple of:
     * the timestamp corresponding to the measurements
     * the system-on-chip temperature in degrees C
     * the tank air temperature in degrees C
     * the depth of the water in inches
     * the uncertainity in the water depth in inches
     * the total water volume in gallons
     * the uncertainity in the total volume in gallons.
    
    If the values cannot be determined all values returned are zero.
    """
    
    t0, s, t, d, de, v, ve = 0, 0, 0, 0, 0, 0, 0
    try:
        with urlopen("http://%s" % ip, timeout=timeout) as uh:
            page = uh.read()
            page = page.decode()
            
        for line in page.split('\n'):
            if line.startswith('SoC'):
                fields = line.split()
                try:
                    s = float(fields[3])
                except:
                    pass
            elif line.startswith('Air'):
                fields = line.split()
                try:
                    t = float(fields[3])
                except:
                    pass
            elif line.startswith('Depth'):
                fields = line.split()
                try:
                    d = float(fields[4])
                    de = float(fields[6])
                except:
                    pass
            elif line.startswith('Current'):
                fields = line.split()
                try:
                    v = float(fields[3])
                    ve = float(fields[5])
                except:
                    pass
            elif line.find(':') != -1:
                t0 = datetime.strptime(line.strip().rstrip().replace('<br>', ''), '%Y/%m/%d %H:%M:%S')
                t0 = pytz.utc.localize(t0).timestamp()
    except:
        pass
        
    return t0, s, t, d, de, v, ve


def get_current_temperature(ip, timeout=30):
    """
    Get the current air temperature in the tanks.  Return a two-
    element tuple of the timestamp when the measurement was made and the
    temperature in degrees C.
    """
    
    t0, s, t, d, de, v, ve = _poll_raincache(ip, timeout=timeout)
    if t0 == 0:
        raise RuntimeError("Failed to get current air temperature")
        
    return t0, t


def get_current_depth(ip, timeout=30):
    """
    Get the current depths of the water.  Returns a three-element tuple of the
    timestamp when the measurement was made, the depth of the water in inches,
    and the uncertainity of the depth in inches.
    """
    
    t0, s, t, d, de, v, ve = _poll_raincache(ip, timeout=timeout)
    if t0 == 0 or d < MIN_VALID_DEPTH:
        raise RuntimeError("Failed to get current water depth")
        
    return t0, d, de


def get_current_volume(ip, timeout=30):
    """
    Get the current total water volume in the tanks.  Returns a three-element
    tuple of the timestamp when the measurement was made, the total volume in
    gallons, and the uncertainity of the volume in gallons.
    """
    
    t0, s, t, d, de, v, ve = _poll_raincache(ip, timeout=timeout)
    if t0 == 0 or d < MIN_VALID_DEPTH:
        raise RuntimeError("Failed to get current water volume")
        
    return t0, v, ve


def _make_plot(db_data):
    """
    Given collection of data that contains values from _poll_raincache(), generate
    a plot that shows the level in the tanks over time.
    """
    
    data = []
    for entry in db_data:
        data.append([entry[key] for key in ('dateTime', 'socTemp', 'airTemp', 'depth', 'depthErr', 'volume', 'volumeErr')])
    data = np.ndarray(data)
    
    # Remove obviously bad data points (depth < 0")
    valid = np.where( data[:,3] >= MIN_VALID_DEPTH )[0]
    data = data[valid,:]*1.0
    
    # Smooth the data over 1 hour windows to reduce the influence of "bad" readings
    data_smooth = np.zeros_like(data)
    data_smooth[:,0] = data[:,0]
    for i in range(data.shape[0]):
        v = np.where( np.abs(data[:,0]-data[i,0]) < 3600 )[0]
        data_smooth[i,1:] = np.median(data[v,:], axis=0)[1:]
    data = data_smooth
    
    # Find data for the last week
    last_week = np.where( np.abs(data[:,0] - data[-1,0]) < 86400*7 )[0]
    
    # Pull out the relevant columns
    t = np.array([datetime.utcfromtimestamp(d) for d in data[:,0]])
    v = data[:,5]
    ve = data[:,6]

    # Fit a line to the volume change over the last week
    v_fit = np.polyfit((data[last_week,0]-data[last_week[-1],0])/86400/7, v[last_week], 1)
    _LOGGER.info('Volume change: %.1f gal/wk', v_fit[0])

    if v_fit[0] < 0:
        t_empty = (500 - v[-1]) / v_fit[0]
        _LOGGER.info('Estimated time until empty: %.1f wk', t_empty)
        
    # Total volume of water as a function of time
    fig = plt.figure()
    ax = fig.gca()
    ax.errorbar(t, v, ve, linestyle='', marker='+')
    ax.plot(t[last_week], np.polyval(v_fit, (data[last_week,0]-data[last_week[-1],0])/86400/7))
    xlim = ax.get_xlim()
    ax.hlines(500, t[0], t[-1], linestyle=':', color='orange')
    ax.set_xlabel('UTC Date')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
    ax.set_xlim(xlim)
    ax.set_ylabel('Total Water Volume [gal]')
    ax.set_ylim((0, 4800))
    ax.set_title('%.1f gal/wk' % v_fit[0])
    fig.autofmt_xdate()
    plt.draw()

    imgname = os.path.abspath(__file__)
    imgname = os.path.join(os.path.dirname(imgname), 'images', 'tanks.png')
    fig.savefig(imgname)


class TankLogger(object):
    """
    Class responsible for monitoring the water levels in the tanks.
    """

    def __init__(self, config, logname=None, scheduler=None):
        self.interval = 600
        self.config = config
        if logname is None:
            logname = '/home/pi/tanks.log'
        self.logname = logname
        self.scheduler = scheduler
        
        self._dbName = os.path.join(os.path.dirname(__file__), 'archive', 'pi2o-tanks.db')
        if not os.path.exists(self._dbName):
            raise RuntimeError(f"TankLogger database '{self._dbName}' not found")
        self._backend = None
        
        self.thread = None
        self.alive = threading.Event()
        
    def start(self):
        if self.thread is not None:
            self.cancel()
            
        if self._backend is None:
            self._backend = DatabaseProcessor(self._dbName)
        self._backend.start()
        
        self.thread = threading.Thread(target=self.run, name='tanks')
        self.thread.setDaemon(1)
        self.alive.set()
        self.thread.start()
        
        _LOGGER.info('Started the TankLogger background thread')
        
    def cancel(self):
        if self.thread is not None:
            self.alive.clear()          # clear alive event for thread
            self.thread.join()
            
        if self._backend is not None:
            self._backend.cancel()
            
        _LOGGER.info('Stopped the TankLogger background thread')
        
    def is_alive(self):
        status = False
        if self.thread is not None:
            status = self.thread.is_alive()
        return status
        
    def run(self):
        self.updatedPlot = datetime.now().replace(year=2000)
        
        last_depth = self.last_entry()[3]
        
        while self.alive.is_set():
            tPoll = time.time()
            tNow = datetime.now()
            tNow = tNow.replace(microsecond=0)
            
            t0, s, t, d, de, v, ve = _poll_raincache(self.config.get('RainCache', 'ip'), timeout=30)
            
            next_sleep = self.interval
            if t0 > 315360000 and d >= MIN_VALID_DEPTH:
                sqlCmd = "INSERT INTO tanks (dateTime,usUnit,socTemp,airTemp,depth,depthErr,volume,volumeErr) VALUES (%f,1,%f,%f,%f,%f,%f,%f)" % (t0, s, t, d, de, v, ve))
                self._backend.append_request(sqlCmd)
                
                if self.scheduler is not None:
                    if self.scheduler.is_watering():
                        next_sleep = 60
                if abs(last_depth - d) > 0.1 and last_depth >= MIN_VALID_DEPTH:
                    next_sleep = min(next_sleep, 120)
                    
                last_depth = d
                
            ## Update the tank plot within one hour of 1 AM
            if tNow - tNow.replace(hour=1, minute=0, second=0) < timedelta(hours=1):
                if tNow - self.updatedPlot >= timedelta(days=1):
                    try:
                        sqlCommand = "SELECT * FROM tanks WHERE dateTime >= %f ORDER BY dateTime DESC" % (time.time()-30*86400)
                        rid = self._backend.append_request(sqlCmd)
                        
                        db_data = self._backend.get_response(rid)
                        _make_plot(db_data)
                        
                        self.updatedPlot = tNow
                        
                    except Exception as e:
                        _LOGGER.warning('Cannot update tank plot, skipping')
                        
            tSleep = next_sleep - (time.time() - tPoll)
            while self.alive.is_set() and tSleep > 0.0:
                time.sleep(min([tSleep, 1.0]))
                tSleep = next_sleep - (time.time() - tPoll)
                
    def last_entry(self):
        """
        Return the last line of the log file as the seven-element tuple that
        _poll_raincache() provides.  See _poll_raincache() for the fields and
        return order.
        """
        
        sqlCmd = 'SELECT * FROM tanks ORDER BY dateTime DESC LIMIT 1'
        rid = self._backend.append_request(sqlCmd)
        
        fields = self._backend.get_response(rid)
        return [fields[-1][key] for key in ('dateTime', 'socTemp', 'airTemp', 'depth', 'depthErr', 'volume', 'volumeErr')]


if __name__ == '__main__':
    t0, s, t, d, de, v, ve = _poll_raincache(sys.argv[1], timeout=30)
    
    if t0 > 315360000 and d >= MIN_VALID_DEPTH:
        with open('/home/pi/tanks.log', 'a') as fh:
            fh.write(f"{t0},{s},{t},{d},{de},{v},{ve}\n")
