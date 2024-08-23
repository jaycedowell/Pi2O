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

__version__ = '0.1'
__all__ = ['MIN_VALID_DISTANCE', 'get_current_temperature', 'get_current_distance', 'get_current_volume']


MIN_VALID_DISTANCE = 4.0    # Inches


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
     * the distance to the water surface in inches
     * the uncertainity in the distance to the water surface in inches
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
            elif line.startswith('Distance'):
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


def get_current_distance(ip, timeout=30):
    """
    Get the current distance from the gauge to the water's surface.  Returns a
    three-element tuple of the timestamp when the measurement was made, the
    distance to the water's surface in inches, and the uncertainity of the
    distance in inches.
    """
    
    t0, s, t, d, de, v, ve = _poll_raincache(ip, timeout=timeout)
    if t0 == 0 or d < MIN_VALID_DISTANCE:
        raise RuntimeError("Failed to get current water surface distance")
        
    return t0, d, de


def get_current_volume(ip, timeout=30):
    """
    Get the current total water volume in the tanks.  Returns a three-element
    tuple of the timestamp when the measurement was made, the total volume in
    gallons, and the uncertainity of the volume in gallons.
    """
    
    t0, s, t, d, de, v, ve = _poll_raincache(ip, timeout=timeout)
    if t0 == 0 or d < MIN_VALID_DISTANCE:
        raise RuntimeError("Failed to get current water volume")
        
    return t0, v, ve


def _make_plot(filename, lock=None):
    """
    Given a logfile that contains values from _poll_raincache(), generate a plot
    that shows the level in the tanks over time.
    """
    
    if lock is not None:
        lock.acquire()
        
    data = np.loadtxt(filename, delimiter=',')
    
    if lock is not None:
        lock.release()
    
    # Remove obviously bad data points (distance to water < 4")
    valid = np.where( data[:,3] >= MIN_VALID_DISTANCE )[0]
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

    def __init__(self, config, logname=None):
        self.interval = 600
        self.config = config
        if logname is None:
            logname = '/home/pi/tanks.log'
        self.logname = logname
        
        self.thread = None
        self.alive = threading.Event()
        self.lock = threading.Lock()
        
    def start(self):
        if self.thread is not None:
            self.cancel()
                   
        self.thread = threading.Thread(target=self.run, name='tanks')
        self.thread.setDaemon(1)
        self.alive.set()
        self.thread.start()
        
        _LOGGER.info('Started the TankLogger background thread')
        
    def cancel(self):
        if self.thread is not None:
            self.alive.clear()          # clear alive event for thread
            self.thread.join()
            
        _LOGGER.info('Stopped the TankLogger background thread')
        
    def is_alive(self):
        status = False
        if self.thread is not None:
            status = self.thread.is_alive()
        return status
        
    def run(self):
        self.updatedPlot = datetime.now().replace(year=2000)
        
        while self.alive.is_set():
            tPoll = time.time()
            tNow = datetime.now()
            tNow = tNow.replace(microsecond=0)
            
            t0, s, t, d, de, v, ve = _poll_raincache(self.config.get('RainCache', 'ip'), timeout=30)
            
            with self.lock:
                if t0 > 0 and d >= MIN_VALID_DISTANCE:
                    with open(self.logname, 'a') as fh:
                        fh.write(f"{t0},{s},{t},{d},{de},{v},{ve}\n")
                        
                trimmed = subprocess.check_output(['tail', '-n2000', self.logname])
                with open(self.logname+'.tmp', 'wb') as fh:
                    fh.write(trimmed)
                os.rename(self.logname+'.tmp', self.logname)
                
            ## Update the tank plot within one hour of 1 AM
            if tNow - tNow.replace(hour=1, minute=0, second=0) < timedelta(hours=1):
                if tNow - self.updatedPlot >= timedelta(days=1):
                    try:
                        _make_plot(self.logname, lock=self.lock)
                        
                        self.updatedPlot = tNow
                        
                    except Exception as e:
                        _LOGGER.warning('Cannot update tank plot, skipping')
                        
            tSleep = self.interval - (time.time() - tPoll)
            while self.alive.is_set() and tSleep > 0.0:
                time.sleep(min([tSleep, 1.0]))
                tSleep = self.interval - (time.time() - tPoll)
                
    def last_entry(self):
        """
        Return the last line of the log file as the seven-element tuple that
        _poll_raincache() provides.
        """
        
        with self.lock:
            last_line = subprocess.check_output(['tail', '-n1', self.logname])
            last_line = last_line.decode().strip().rstrip()
            fields = [float(v) for v in last_line.split(',')]
        return fields


if __name__ == '__main__':
    t0, s, t, d, de, v, ve = _poll_raincache(sys.argv[1], timeout=30)
    
    if t0 > 315360000 and d >= MIN_VALID_DISTANCE:
        with open('/home/pi/tanks.log', 'a') as fh:
            fh.write(f"{t0},{s},{t},{d},{de},{v},{ve}\n")
