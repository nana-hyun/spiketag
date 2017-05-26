import os
import numpy as np
from numba import jit
from multiprocessing import Pool
from .SPK import SPK
from .Binload import bload

@jit(cache=True)
def _to_spk(data, pos, chlist, spklen=19, prelen=8):
    n = len(pos)
    spk = np.empty((n, spklen, len(chlist)), dtype=np.float32)
    for i in range(n):
        # i spike in chlist
        spk[i, ...]  = data[pos[i]-prelen:pos[i]-prelen+spklen, chlist]
    _nan = np.where(chlist==-1)[0]
    spk[..., _nan] = 0
    return spk

@jit(cache=True, nopython=True)
def _broaden_pivotal(origin_pivotal):
    '''
        Only for tetrode.
        The origin pivotal is grouped, but when do sorting, should ungroup the pivotal, the
        time at every channel is aligned by grouped pivotal value.
    '''
    broadening = np.hstack((origin_pivotal, origin_pivotal, origin_pivotal, origin_pivotal))
    for i in range(origin_pivotal.shape[1]):
        chs = np.arange(origin_pivotal[1, i]/4*4, origin_pivotal[1, i]/4*4+4)
        for idx, val in enumerate(chs):
            broadening[1, (idx*origin_pivotal.shape[1] + i)] = val
    return broadening

class MUA():
    def __init__(self, filename, probe, numbytes=4, binary_radix=14):
        
        self.nCh = probe.n_ch
        self.ch  = range(self.nCh)
        self.fs  = probe.fs*1.0
        self.probe = probe
        self.numbytes = numbytes
        self.dtype = 'i'+str(self.numbytes)
        self.bf = bload(self.nCh, self.fs)
        self.bf.load(filename, dtype=self.dtype)
        self.filename = filename
        self.data = self.bf.asarray(binpoint=binary_radix)
        self.t    = self.bf.t

        self.npts = self.bf._npts
        self.spklen = 19
        self.prelen = 8
        spk_meta = np.fromfile(filename+'.spk', dtype='<i4')
        self.pivotal_pos = spk_meta.reshape(-1,2).T

        if self.probe.type == 'tetrode':
            self.pivotal_pos = _broaden_pivotal(self.pivotal_pos)

    def tospk(self):
        self.ch_hash = np.asarray([self.probe.get_group(ch) 
                                                for ch in range(self.nCh)])
        spkdict = {}
        for ch in range(self.nCh):
            pos = self.pivotal_pos[0, self.pivotal_pos[1]==ch]
            spkdict[ch] = _to_spk(data   = self.data, 
                                  pos    = pos, 
                                  chlist = self.ch_hash[ch], 
                                  spklen = self.spklen,
                                  prelen = self.prelen)
                                 
        return SPK(spkdict)

    def get_nid(self, corr_cutoff=0.95):  # get noisy spk id
        # 1. dump spikes file (binary)
        piv = self.pivotal_pos.T
        nspk = self.pivotal_pos.shape[1]
        # the reason adding mod operation here is if the spike is in the very end ,i.e: within 15 offset to end 
        # point, this will make self.data[rows, :] out of bound.
        rows = (np.arange(-10,15).reshape(1,-1) + piv[:,0].reshape(-1,1)) % self.data.shape[0]
        cols = piv[:,1].reshape(-1,1)
        full_spk = self.data[rows, :]
        filename = os.path.dirname(self.filename)+'/.'+os.path.basename(self.filename)+'.spkfull'
        full_spk.tofile(filename)

        # 2. parallel screen the noise id out, then gather from CPUs
        from ipyparallel import Client
        from ipyparallel.util import interactive
        rc = Client()
        cpu = rc[:]
        cpu.block = True

        @cpu.remote(block=True)      # to be executed by cpu
        @interactive                 # to be on the global()
        def get_noise_ids(filename, corr_cutoff):
            spk_data = np.memmap(filename, dtype='f4').reshape(-1, 25, 32)
            noise_id = []
            # corr_cutoff = 0.98
            # ind is index assign to each cpu
            # corr_cutoff is threshold of corr_coef
            for i in ind:
                spikes = spk_data[i]
                seq = np.abs(np.corrcoef(spikes[5:15, 16:].T).ravel())
                seq[seq>corr_cutoff] = 1
                seq[seq<corr_cutoff] = 0
                if np.median(seq) == 1.0:
                    noise_id.append(i)
            return noise_id

        # f = interactive(get_noise_ids)
        cpu.execute('import numpy as np')
        cpu.scatter('ind', range(nspk))
        noise_id = get_noise_ids(filename, corr_cutoff)
        # cpu.execute("%reset")
        try:
            os.remove(filename)
        except OSError:
            pass
        return np.hstack(np.asarray(noise_id))


    def remove_high_corr_noise(self, corr_cutoff=0.95):
        nid = self.get_nid(corr_cutoff)
        self.pivotal_pos = np.delete(self.pivotal_pos, nid, axis=1)
