import numpy as np
import torch
import torch.nn as nn
from scipy.stats import zscore
from scipy import signal
from ..analysis import smooth, get_cwt, spike_unit, rank_array
from scipy.ndimage import gaussian_filter1d
from spiketag.view.color_scheme import palette
import matplotlib.colors as colors
from matplotlib.ticker import ScalarFormatter, MultipleLocator

# define a time series class
class TimeSeries(object):
    '''
    Time series with different sampling rate, different start offset, and different length can cause headache when 
    analyzing and visualizing them. This class is designed to extract and align time series without resampling. 

    sig = TS(t, data, name='sig')
    sig_ROI = sig.between(0.5, 1.5) # return another TS object with t and data between 0.5 and 1.5
    sig_ROI.plot(ax=ax) # plot the ROI

    Examples:
    ---------
    # 1. load data from different sources (with different sampling rate)
    lfp = TS(t = lfp_ch18['t'], data = lfp_ch18['lfp'], name='lfp')
    unit = UNIT(bin_len=0.1, nbins=50) # 100ms bin, 50 bins
    unit.load_unitpacket('./fet.bin')
    bmi_time = unit.bin_index/10
    hdv = np.fromfile('./animal_hdv.bin', np.float32).reshape(-1,2)
    hd, v = hdv[:,0] - 90, hdv[:,1]

    # 2. load lfp,spk,vel as a TimeSeries object
    lfp = TS(t = lfp_18['t'], data = lfp_18['lfp'], name='lfp')
    spk = TS(t = unit.spike_time[unit.spike_id!=0], data = unit.spike_id[unit.spike_id!=0], name='spike_timing')
    vel = TS(t = bmi_time, data=v, name='ball_velocity')

    # 3. extract common ROI
    t_start, t_end = 737.6, 738.6
    _lfp = lfp.between(t_start, t_end)
    _spk = spk.between(t_start, t_end)
    _bv  = vel.between(t_start, t_end)

    ### check the ROI time points, they can be very different: _lfp.t.shape, _bv.t.shape, _spk.t.shape

    # 4. plot together although they have different length in the same time period
    fig, ax = plt.subplots(3,1,figsize=(15,8), sharex=True)
    _spk.scatter(ax = ax[0], c=_spk.data, s=2, cmap='Set2')
    _lfp.plot(ax = ax[1]);
    _bv.plot(ax = ax[2]);
    ax[0].spines['bottom'].set_visible(False)
    ax[1].spines['bottom'].set_visible(False)
    fig.tight_layout()
    ---------
    '''
    def __init__(self, t, data, name=None):
        self.t = t
        self.data = data
        self.name = name
        # self.fs = 1/(t[1]-t[0])
        self.ndim = self.data.ndim
        if self.ndim == 1:
            self.data = self.data.reshape(-1, 1)
        self.nch = self.data.shape[1]
        assert(len(self.t) == self.data.shape[0]), 'time and data length do not match'

    def select(self, feature_idx):
        return TimeSeries(self.t, self.data[:, feature_idx], self.name)

    def between(self, start_time, end_time):
        idx = np.where((self.t >= start_time) & (self.t <= end_time))[0]
        return TimeSeries(self.t[idx], self.data[idx], self.name)
    
    def exclude(self, start_time, end_time):
        idx = np.where((self.t < start_time) | (self.t > end_time))[0]
        return TimeSeries(self.t[idx], self.data[idx], self.name)
    
    def find_peaks(self, z_high=None, z_low=1, **kwargs):
        peaks, left, right = {}, {}, {}
        for ch in range(self.nch):
            if z_high is not None:
                self.threshold = np.median(self.data[:,ch]) + z_high * np.std(self.data[:,ch])
                _peaks_idx, _ = signal.find_peaks(self.data[:,ch], height=self.threshold, **kwargs)
            else:
                _peaks_idx, _ = signal.find_peaks(self.data[:, ch], **kwargs)
            _left_idx, _right_idx = self.find_left_right_nearest(
                np.where(self.data[:, ch] < z_low * np.std(self.data[:, ch]))[0][:-1], _peaks_idx)
            peaks[ch] = TimeSeries(self.t[_peaks_idx], self.data[_peaks_idx], self.name+'_peaks_'+str(ch))
            left[ch]  = TimeSeries(self.t[_left_idx], self.data[_left_idx], self.name+'_left_'+str(ch))
            right[ch] = TimeSeries(self.t[_right_idx], self.data[_right_idx], self.name+'_right_'+str(ch))
        return peaks, left, right

    def find_left_right_nearest(self, x_idx, v_idx):
        """
        Find the adjacent index of v_idx (N,) in x_idx (return the N left index of a, and N right index of a)
        """
        _idx_right = np.searchsorted(x_idx, v_idx)
        _idx_left = np.searchsorted(x_idx, v_idx) - 1
        left = x_idx[_idx_left] - 1
        right = x_idx[_idx_right] 
        return left, right

    def filtfilt(self, N=20, Wn=[100, 300], type='bp', fs=None, show=False):
        if fs is None:
            fs = 1/(self.t[1]-self.t[0])

        b, a = signal.butter(N, Wn, btype=type, fs=fs)
        y = signal.filtfilt(b, a, self.data, axis=0)

        if show is True:
            import matplotlib.pyplot as plt
            w, h = signal.freqz(b, a, fs=fs)
            plt.plot(w, 20 * np.log10(abs(h)))
            plt.axvspan(Wn[0], Wn[1], alpha=0.5)
            plt.title('Butterworth filter frequency response')
            plt.xlabel('Frequency [Hz]')
            plt.ylabel('Amplitude [dB]')
        return TimeSeries(self.t, y, self.name+'_filtered'+str(Wn))

    def hilbert(self, **kwargs):
        '''
        self.data must be 1-d numpy array
        '''
        amplitude_envelope = np.abs(signal.hilbert(self.data.ravel(), **kwargs))
        return TimeSeries(self.t, amplitude_envelope , self.name+'_hilbert')

    def zscore(self, **kwargs):
        return TimeSeries(self.t, zscore(self.data, **kwargs), self.name+'_zscore')
    
    def smooth(self, n=5, type='gaussian'):
        '''
        - for gaussian, n is sigma
        - for boxcar,   n is window length
        '''
        if type=='boxcar':
            data = smooth(self.data.astype(np.float32), n)
            return TimeSeries(self.t, data, self.name+f'_smooth_{n}')
        elif type=='gaussian':
            data = gaussian_filter1d(self.data.astype(np.float32), sigma=n, axis=0, mode='constant')
            return TimeSeries(self.t, data, self.name+f'_gaussian_smooth_{n}')
    
    def sum(self):
        return TimeSeries(self.t, self.data.sum(axis=1), self.name+'_sum')

    def min_subtract(self):
        return TimeSeries(self.t, self.data - np.min(self.data, axis=0), self.name+'_mean_subtract')

    def max_subtract(self):
        return TimeSeries(self.t, self.data - np.max(self.data, axis=0), self.name+'_mean_subtract')

    def mean_subtract(self):
        return TimeSeries(self.t, self.data - np.mean(self.data, axis=0), self.name+'_mean_subtract')

    def get_cwt(self, fmin=0, fmax=128, dj=1/100, show=False):
        cwtmatr = get_cwt(self.t, self.data.ravel(), fmin=fmin, fmax=fmax, dj=dj)
        if show is True:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(2,1,figsize=(16,8), sharex=True)
            self.plot(ax = ax[0]);
            ax[1].pcolormesh(cwtmatr.t, cwtmatr.freq, cwtmatr.magnitude, cmap='viridis');
        return cwtmatr

    def plot(self, ax=None, **kwargs):
        if ax is None:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(1,1,figsize=(12,5))
        ax.plot(self.t, self.data, **kwargs)
        return ax

    def scatter(self, ax=None, **kwargs):
        if ax is None:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(1,1,figsize=(12,5))
        ax.scatter(self.t, self.data, **kwargs)
        return ax

    @property
    def shape(self):
        return self.data.shape

    def __getitem__(self, idx):
        return TimeSeries(self.t[idx], self.data[idx], self.name)

    def __len__(self):
        return len(self.t)

    def __repr__(self):
        return f'{self.name} from {self.t[0]} to {self.t[-1]}'

    def __add__(self, other):
        return TimeSeries(self.t, self.data + other.data, self.name)

    def __sub__(self, other):
        return TimeSeries(self.t, self.data - other.data, self.name)

    def __mul__(self, other):
        return TimeSeries(self.t, self.data * other.data, self.name)

    def __truediv__(self, other):
        return TimeSeries(self.t, self.data / other.data, self.name)

    def __pow__(self, other):
        return TimeSeries(self.t, self.data ** other.data, self.name)

    def __neg__(self):
        return TimeSeries(self.t, -self.data, self.name)
    
    def __abs__(self):
        return TimeSeries(self.t, np.abs(self.data), self.name)

    def __eq__(self, other):
        return TimeSeries(self.t, self.data == other.data, self.name)

    def __ne__(self, other):
        return TimeSeries(self.t, self.data != other.data, self.name)

    def __lt__(self, other):
        return TimeSeries(self.t, self.data < other.data, self.name)

    def __le__(self, other):
        return TimeSeries(self.t, self.data <= other.data, self.name)

    def __gt__(self, other):
        return TimeSeries(self.t, self.data > other.data, self.name)

    def __ge__(self, other):
        return TimeSeries(self.t, self.data >= other.data, self.name)

    def __and__(self, other):
        return TimeSeries(self.t, self.data & other.data, self.name)

    def __or__(self, other):
        return TimeSeries(self.t, self.data | other.data, self.name)

    def __xor__(self, other):
        return TimeSeries(self.t, self.data ^ other.data, self.name)

    def __invert__(self):
        return TimeSeries(self.t, ~self.data, self.name)

    def __lshift__(self, other):
        return TimeSeries(self.t, self.data << other.data, self.name)

    def __rshift__(self, other):
        return TimeSeries(self.t, self.data >> other.data, self.name)

    def __iadd__(self, other):
        self.data += other.data
        return self

    def __isub__(self, other):
        self.data -= other.data
        return self

    def __imul__(self, other):
        self.data *= other.data
        return self

    def __itruediv__(self, other):
        self.data /= other.data
        return self

    def __ipow__(self, other):
        self.data **= other.data
        return self

    def __iand__(self, other):
        self.data &= other.data
        return self

    def __ior__(self, other): 
        self.data |= other.data
        return self

    def __ixor__(self, other):
        self.data ^= other.data
        return self

    def __ilshift__(self, other):
        self.data <<= other.data
        return self

    def __irshift__(self, other):
        self.data >>= other.data
        return self

class spike_train(TimeSeries):
    """
    This class is used for spike train analysis.

    Args:

    spike_time: a numpy array of spike times of N neurons.
    spike_id  : a numpy array of spike id    of N neurons.
    For example, stack time and id into one matrix (of two rows, first row is time, second row is id):
        array([[ 0.   ,  0.   ,  0.   , ...,  0.398,  0.398,  0.399],
               [41.   , 43.   , 71.   , ..., 70.   , 77.   , 10.   ]],
        dtype=float32)

    Attributes:
        spike_time: a numpy array of spike times of N neurons (spk_time in #samples)
        spike_id  : a numpy array of spike id    of N neurons
        unit: a dictionary of spike_unit
        unit[i] is the spike_unit of neuron i
        unit[i].id: the id of neuron i
        unit[i].spk_time: the spike time of neuron i
    """

    def __init__(self, spike_time, spike_id, name=None):
        """
        This class is used for spike train analysis.
        """
        self.spike_time = spike_time
        self.spike_id = spike_id
        self.unit = {}
        for i in np.unique(self.spike_id):
            self.unit[i] = spike_unit(spk_id=i,
                                      spk_time=self.spike_time[self.spike_id == i])
        super(spike_train, self).__init__(self.spike_time, self.spike_id, name)
        
        # estimiate mua
        tmin = self.t.min()*10//10
        tmax = self.t.max()*10//10
        self.mua = self.get_mua_fr(tmin, tmax, t_step=25e-3, std=25e-3) 

    def between(self, start_time, end_time):
        idx = np.where((self.t >= start_time) & (self.t <= end_time))[0]
        return spike_train(self.t[idx], self.data[idx].ravel(), self.name)
    
    def exclude(self, start_time, end_time):
        """Exclude spikes between start_time and end_time.

        Parameters
        ----------
        start_time : float or numpy array
            Start time(s) of the time interval(s) to exclude.
        end_time : float or numpy array
            End time(s) of the time interval(s) to exclude.

        Returns
        -------
        spike_train
            A new spike_train object with the spikes between start_time and end_time excluded.

        Examples
        --------
        spk.between(1290, 1400).exclude(1295, 1335).exclude(1360, 1369).scatter(s=1);
        spk.between(1290, 1400).exclude([1295, 1360], [1335, 1369]).scatter(s=1);

        Should be the same, but the second one is way faster.

        """
        start_time = np.array(start_time)
        end_time = np.array(end_time)
        if start_time.ndim == 0:
            start_time = start_time.reshape(1)
        if end_time.ndim == 0:
            end_time = end_time.reshape(1)

        # the code iterates over the elements of start_time and end_time, and selects the elements of 
        # self.t and self.data that are outside the corresponding time interval.
        # The np.intersect1d() function is used to intersect the indices of the selected elements, 
        # so that only the spikes that are outside all the time intervals are excluded.
        # Finally, the selected elements are used to create a new spike_train object.

        idx = np.arange(len(self.t))
        for i in range(len(start_time)):
            _idx = np.where((self.t < start_time[i]) | (self.t > end_time[i]))[0]
            idx = np.intersect1d(idx, _idx)  
        return spike_train(self.t[idx], self.data[idx].ravel(), self.name)

    def select(self, neuron_idx):
        '''
        return a new spike_train object with only the selected neurons
        '''
        return spike_train(self.t[np.isin(self.data.ravel(), neuron_idx)], 
                           self.data.ravel()[np.isin(self.data.ravel(), neuron_idx)], self.name)

    def sort(self, sorted_idx='ascending'):
        '''
        Returns a new spike_train object with the sorted spike-id is the rank of the original spike-id. Resulting the np.unique(spike_id) in a continuous sequence.

        - The original unique spike ids may not be continuous and may have gaps, such as [3, 5, 11, 28, 234, 248].
        - This method assign each spike ID a rank and returns a new spike_train object with the ranked IDs, such that the new unique spike rank-id starts
          from 0 and is continuous. 

        The original spike-id is useful for accessing representation/analysis
        The ranked spike-id is useful for ordering in sequences, visualization etc., which are better in a continuous id space. 

        if sorted_idx == ascending, it returns the ranked spike-id according to how big the spike id is. 
        if sorted_idx is a numpy array, it returens the ranked spike-id in the sorted_idx using np.searchsorted. 
        '''
        if sorted_idx == "ascending":
            self.spike_id_rank = rank_array(self.data.ravel())
        if type(sorted_idx) == np.ndarray:
            self.spike_id_rank = np.searchsorted(sorted_idx, self.data.ravel())

        if self.name is not None:
            return spike_train(self.t, self.spike_id_rank, self.name+'_ranked')
        else:
            return spike_train(self.t, self.spike_id_rank, 'spike_train_ranked')

    def __len__(self):
        return len(self.t)

    def __getitem__(self, i):
        return self.unit[i]

    @property
    def neuron_idx(self):
        return np.unique(self.spike_id)

    @property
    def n_units(self):
        return len(self.neuron_idx)

    @property
    def max_unit_id(self):
        return max(self.neuron_idx)

    ### get statistics/varibles ### 

    def get_scv(self, start_time, end_time, t_step=100e-3):
        '''
        when t_step=100e-3, this function should produce the same result as bmi_scv_full[:, -1, :], bmi_scv_full = np.fromfile('./scv.bin').reshape(-1, B_bins, n_units) 
        '''
        ts = np.arange(start_time-t_step, end_time, t_step-1e-15)
        spike_time = self.t
        spike_id = self.data.ravel()
        self.scv = np.vstack([np.histogram(spike_time[spike_id==i], ts)[0] for i in self.neuron_idx]).T
        return ts[1:], self.scv

    def get_sua_mean_fr(self, start_time=None, end_time=None):
        '''
        mean firing rate is total spike count of a neuron divided by the total time of the spike train
        each unit should have the same time length (end_time - start_time) using behavior time !!!
        '''
        if start_time is None:
            start_time = self.t.min()
        if end_time is None:
            end_time = self.t.max()
        _spk = self.between(start_time, end_time)
        mean_spike_rate = np.array([np.sum(_spk.data.ravel() == i) for i in self.neuron_idx])/(end_time - start_time)
        return mean_spike_rate

    def get_sua_fr(self, start_time=None, end_time=None, t_step=100e-3, std=100e-3, zscore=False):
        '''
        std: the std for gaussian smoothing window, using the same time unit as t_step 
        '''
        if start_time is None:
            start_time = self.t.min()
        if end_time is None:
            end_time = self.t.max()
        ts, scv = self.get_scv(start_time, end_time, t_step)       # spike count vector
        scv_rate  = TimeSeries(t=ts, data=scv/t_step, name='scv')  # spike count vector / t_step = spike count rate
        sigma = std/t_step
        units_firing_rates = scv_rate.smooth(sigma)                  # smooth using a gaussian window with std sigma length
        if zscore:                                          
            units_firing_rates = units_firing_rates.zscore()       # z-score the rate (if zscore is true)
        return units_firing_rates

    def get_mua_fr(self, start_time=None, end_time=None, t_step=25e-3, std=25e-3, zscore=False):
        '''
        std: the std for gaussian smoothing window
        '''
        if start_time is None:
            start_time = self.t.min()
        if end_time is None:
            end_time = self.t.max()
        ts, scv = self.get_scv(start_time, end_time, t_step)
        scv_fr  = TimeSeries(t=ts, data=scv/t_step, name='scv')    
        sigma = std/t_step    
        mua_fr = scv_fr.sum().smooth(sigma)
        if zscore:
            mua_fr = mua_fr.zscore()
        return mua_fr

    def get_mua_bursts(self, start_time=None, end_time=None, t_step=25e-3, std=25e-3, zscore=True, height=2, z_low=1):
        '''
        Get the time periods of mua bursts, where the mua firing rate is above the threshold
        returns the start and end time of each population burst, in the format that can be used in spike_train.exclude(start_time, end_time)

        Parameters:
        ----------
            # - start_time: start time of the period to be analyzed
            # - end_time: end time of the period to be analyzed
            # - t_step: time step for calculating the mua firing rate
            # - std: std for gaussian smoothing window
            # - zscore: if true, zscore the mua firing rate before detecting bursts
            # - threshold: threshold for detecting bursts, in z-scored or raw mua firing rate
            # - z_low: threshold for detecting the start and the end of a burst, in z-score of standard deviation of mua firing rate (default: 1)

        Returns:
        ----------
            # - burst_start_time: a numpy array of start time of each burst
            # - burst_end_time: a numpy array of end time of each burst
            # - burst_peak_time: a numpy array of peak time of each burst
        '''
        tmin = self.t.min()*10//10
        tmax = self.t.max()*10//10
        if start_time is None:
            start_time = tmin
        if end_time is None:
            end_time = tmax
        mua_fr = self.get_mua_fr(start_time, end_time, t_step, std, zscore)
        peak_idx, left_idx, right_idx = mua_fr.find_peaks(height=height, z_low=1)
        peak_idx, left_idx, right_idx = peak_idx[0], left_idx[0], right_idx[0]
        self.mua_left_time = left_idx.t
        self.mua_right_time = right_idx.t
        self.mua_peak_time = peak_idx.t
        
        return self.mua_left_time, self.mua_right_time, self.mua_peak_time

    ### API to other libraries ###

    def to_neo(self, start_time=None, end_time=None):
        '''
        convert to a list of neo spike trains
        https://neo.readthedocs.io/en/stable/core.html

        neo statistic method can be direcly applied
        https://elephant.readthedocs.io/en/latest/tutorials/statistics.html
        '''
        if start_time is None:
            start_time = self.t.min()
        if end_time is None:
            end_time = self.t.max()

        _spk = self.between(start_time, end_time)
        neo_spike_train = []
        for i in self.neuron_idx:
            neo_spike_train.append(_spk[i].to_neo(time_units='sec', t_start=start_time, t_stop=end_time))
        return neo_spike_train

    ### visualization methods ###

    def eventplot(self, figsize=(8,3), unit_id_label_freq=1, start_time=None, end_time=None):
        if start_time is None:
            start_time = 0
        if end_time is None:
            end_time = self.t[-1]
        _spk = self.between(start_time, end_time)

        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1,1,figsize=figsize)
        ax.eventplot([_spk[i].t for i in _spk.neuron_idx], linelengths=0.75, lineoffsets=_spk.neuron_idx, color='black')
        ax.yaxis.set_major_locator(MultipleLocator(unit_id_label_freq))
        return ax

    def show(self, start_time=None, end_time=None, ax=None, fig_height=5, fig_width=2, unit_id_label_freq=50, s=5, marker='|', t_step=25e-3, std=25e-3, **kwargs):
        '''
        return two axes:
        ax[0]: raster plot of spike trains (scatter)
        ax[1]: line plot of mua rate (plot)

        use
        ax[0].get_figure() to get fig
        '''
        if start_time is None:
            start_time = self.t.min()
        if end_time is None:
            end_time   = self.t.max()
        if ax is None:
            import matplotlib.pyplot as plt
            # fig, ax = plt.subplots(2,1,figsize=(12,5))
            fig = plt.figure(constrained_layout=False, figsize=(fig_width*3, fig_height*3))
            gs1 = fig.add_gridspec(nrows=fig_height, ncols=1, hspace=0.05)
            ax0 = fig.add_subplot(gs1[:-1, :]) # raster plot of spikes
            ax0.set_facecolor((0.0, 0.0, 0.0))
            ax0.yaxis.set_major_locator(MultipleLocator(unit_id_label_freq))
            ax1 = fig.add_subplot(gs1[-1, :])  # plot mua 
            ax = [ax0, ax1]
        _spk = self.between(start_time, end_time)
        _mua = self.get_mua_fr(self.t.min(), self.t.max(), t_step=t_step, std=std).between(start_time, end_time) # get mua to plot the population firing rate
        ax[0].scatter(_spk.t, _spk.data.ravel(), c=_spk.data.ravel(), s=s, marker=marker, cmap=colors.ListedColormap(palette), **kwargs)
        ax[0].set_xticks([])
        ax[0].set_ylim(0, self.max_unit_id+1)
        ax[1].plot(_mua.t, _mua.data.ravel())
        ax[0].set_xlim(start_time, end_time)
        ax[1].set_xlim(start_time, end_time)
        # ax[1].set_xlabel('Time (s)')
        return ax

    def plot_mua_bursts(self, ax):
        '''
        assume self.mua_left_time, self.mua_right_time, self.mua_peak_time are already computed
        self.get_mua_bursts() should be called before this function
        '''

        # plot mua bursts in a list of axes
        if ax is not None and type(ax) is list:
            for _ax in ax:
                _ax_t_start, _ax_t_end = _ax.get_xlim()
                for mua_burst_idx in np.where((self.mua_peak_time < _ax_t_end) & (self.mua_peak_time > _ax_t_start))[0]:
                    _ax.axvspan(
                        self.mua_left_time[mua_burst_idx], self.mua_right_time[mua_burst_idx], alpha=0.3)
        # plot mua bursts in one axis
        elif ax is not None and type(ax) is not list:
            _ax_t_start, _ax_t_end = ax.get_xlim()
            for mua_burst_idx in np.where((self.mua_peak_time < _ax_t_end) & (self.mua_peak_time > _ax_t_start))[0]:
                ax.axvspan(
                    self.mua_left_time[mua_burst_idx], self.mua_right_time[mua_burst_idx], alpha=0.3)