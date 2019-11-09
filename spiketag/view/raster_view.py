import numpy as np
from ..base import CLU
from .color_scheme import palette
from .scatter_2d_view import scatter_2d_view

class raster_view(scatter_2d_view):

    def __init__(self, fs=25e3, time_tick=1):
        super(raster_view, self).__init__(symbol='|', marker_size=6., edge_width=1e-3)
        super(raster_view, self).attach_xaxis()

        self._time_tick = time_tick 
        self._fs = fs

    ### ----------------------------------------------
    ###              public method 
    ### ----------------------------------------------

    def set_data(self, spkid_matrix):
        '''
        spkid_matrix: n*2 matrix, n spikes, first column is #sample, second column is the spike id
        '''
        self._spike_time = spkid_matrix[:,0] 
        self._clu = CLU(spkid_matrix[:,1])
        self._draw(self._clu.index_id)


    @property
    def binsize(self):
        return int(self._fs * self._time_tick)

    def highlight(self, global_idx):
        ''' Transform the global idx to the view idx:
                Listen the select event from other view, and find the intersect spikes in current clus which selected to display within amplitude view. 
        '''
        # find the intersect cluster between other view and amplitude view
        local_idx = self._clu.global2local(global_idx)
        current_clus = self._clu.select_clus
        common_clus = np.intersect1d(current_clus, np.array(local_idx.keys()))
        
        # the spike idx in parent-class is |cluster1|cluster2|cluster3|....|,
        # so the local idx in cluster2 is need to plus len(cluster1)
        view_idx = np.array([],dtype='int64')
        if len(common_clus) > 0:
            for clu in common_clus:
                before = current_clus[np.where(current_clus < clu)]
                for b in before:
                    local_idx[clu] += self._clu.index_count[b]
                view_idx = np.hstack((view_idx, local_idx[clu]))
        
        super(raster_view, self)._highlight(view_idx)

    def select(self, view_idx):
        ''' 
            Transfrom the view idx to the global idx.
        '''
        # all clusters within the view currently
        current_clus = self._clu.select_clus
        local_idx = {}
        
        # assign idx to different range |cluster1|cluser2|cluster3|....|
        # according the length of cluster
        left = 0
        for clu in current_clus:
            right = left + self._clu.index_count[clu]
            index = view_idx[(view_idx>=left)&(view_idx<right)]
            if len(index) >  0:
                local_idx[clu] = index - left
            left = right
        global_idx = self._clu.local2global(local_idx)
        self._clu.select(global_idx, caller=self.__module__)


 
    ### ----------------------------------------------
    ###              private method 
    ### ----------------------------------------------

    def _draw(self, clus, delimit=True):
       
        poses = None
        colors = None
        span = 10. / len(self._clu.index_id)

        for clu in clus:
            times = self._spike_time[self._clu.index[clu]]
            x, y = times / self.binsize, np.full(times.shape, clu * span)
            pos = np.column_stack((x,y))
            color = np.tile(np.hstack((palette[clu],1)),(pos.shape[0],1))

            if poses is None and colors is None:
                poses = pos
                colors = color
            else:
                poses = np.concatenate((poses, pos))
                colors = np.concatenate((colors, color))
        
        super(raster_view, self).set_data(pos=poses, colors=colors, delimit=delimit)
