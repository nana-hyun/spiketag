import numpy as np
from ..utils.utils import EventEmitter
from ..utils.utils import Timer
from ..utils.conf import error, info, debug

def instack_membership(func):
    def wrapper(self, *args, **kwargs):
        self._membership_stack.append(self.membership.copy())
        return func(self, *args, **kwargs)
    return wrapper


class CLU(EventEmitter):
    """docstring for Clu"""
    def __init__(self, clu, method=None, clusterer=None, treeinfo=None, probmatrix=None):
        super(CLU, self).__init__()
        self.membership = clu.copy()
        if method:
            self._method = method
        if clusterer:
            self._extra_info = self._extract_extra_info(clusterer)       
            self._select_clusters = self._extra_info['default_select_clusters']
        if treeinfo:
            self._extra_info = treeinfo
            self._select_clusters = treeinfo['default_select_clusters']
        if probmatrix is not None:
            self._probmatrix = probmatrix

        self.__membership = self.membership.copy()
        while min(self.membership) < 0:
            self.membership += 1
        self._membership_stack = []
        self.__construct__()
        self.selectlist = np.array([])

    @property
    def npts(self):
        return self.membership.shape[0]

    def __construct__(self):
        '''
        construct the base properties
        nclu:        number of clusters
        index:       dict of {id: index}
        index_count: dict of {id: counts}
        index_id:    array of id
        '''
        self.index       = {}
        self.index_count = {}
        self.index_id    = np.unique(self.membership)
        self.index_id.sort()
        self.make_id_continuous()
        # all clus are selected default
        self.select_clus = self.index_id
        self.nclu        = len(self.index_id)
        _counts_per_clu = [0,]
        for cluNo in self.index_id:
                self.index[cluNo] = np.where(self.membership==cluNo)[0]
                self.index_count[cluNo] = len(self.index[cluNo])
                _counts_per_clu.append(self.index_count[cluNo])
        self._clu_cumsum = np.cumsum(np.asarray(_counts_per_clu))

    def _extract_extra_info(self, clusterer):
        '''store extra infomation for other purpose.
        '''
        extra_info = {}
        extra_info['condensed_tree'] = clusterer._condensed_tree
        extra_info['default_select_clusters'] = np.sort(np.array(clusterer.condensed_tree_._select_clusters(), dtype=np.int64))
        return extra_info

    def make_id_continuous(self):
        if self._is_id_discontineous():
            for i in range(1, self.index_id.size):
                self.membership[self.membership==self.index_id[i]] = i
            self.index_id    = np.unique(self.membership)
            self.index_id.sort()
    
    def global2local(self,global_idx):
        '''
            get the local id and clu_no from the global idx, the result is dist,eg:
            {0:[1,2,3],1:[1,2,3]}
        '''
        local_idx = {}

        if isinstance(global_idx,int):
            global_idx = np.array([global_idx])

        if isinstance(global_idx, np.int64):
            global_idx = np.array([global_idx])

        if isinstance(global_idx, np.int32):
            global_idx = np.array([global_idx])

        if len(global_idx):
            clus_nos = np.unique(self.membership[global_idx])
      
            if len(clus_nos) == 1:
                clus_no = int(clus_nos[0])
                sub_local_idx = np.searchsorted(self.index[clus_no], global_idx)
                sub_local_idx.sort()
                local_idx[clus_no] = sub_local_idx 
            else:
                for clus_no in clus_nos:
                    clus_no = int(clus_no)
                    local_global_idx = np.intersect1d(self.index[clus_no],global_idx,assume_unique=True)
                    sub_local_idx = np.searchsorted(self.index[clus_no], local_global_idx)
                    sub_local_idx.sort()
                    local_idx[clus_no] = sub_local_idx
        
        return local_idx
 
        
    def local2global(self,local_idx):
        '''
            get the global id from the clus, the clus is a dict, including the clu_no and sub_idex,eg:
            {0:[1,2,3],1:[1,2,3]}
        '''
        global_idx = np.array([],dtype='int')

        for clu_no, local_idx in local_idx.items():
            global_idx = np.append(global_idx,self._glo_id(clu_no, local_idx, sorted=False))
        global_idx.sort()

        return global_idx
            

    def _is_id_discontineous(self):
        if any(np.unique(self.index_id[1:]-self.index_id[:-1]) > 1):
            return True
        else:
            return False

    def __getitem__(self, i):
        assert i in self.index_id, "{} should in {}".format(i, self.index_id)
        return self.index[i]

    def __str__(self):
        for cluNo, index in self.index.items():
            info(cluNo, index)
        return str(self.index_count)

    def _glo_id(self, selected_clu, sub_idx, sorted=True):
        '''
        get the global id of a subset in selected_clu
        '''
        if type(sub_idx) is not list:
            sub_idx = list(sub_idx)
        glo_idx = self.index[selected_clu][sub_idx]
        if sorted:
            glo_idx.sort()
        return glo_idx                

    def _sub_id(self, global_idx):
        '''
        get the local id and sub_clu from the global idx
        '''
        sub_clu = np.unique(self.membership[global_idx])
        if len(sub_clu) == 1:
            sub_clu = int(sub_clu)
            sub_idx = np.searchsorted(self.index[sub_clu], global_idx)
            sub_idx.sort()
            return sub_clu, sub_idx
        else:
            info('goes to more than one cluster')


    def select(self, selectlist, caller=None):
        '''
            select global_idx of spikes
        '''
        self.selectlist = np.unique(selectlist) # update selectlist
        self.emit('select', action='select', caller=caller)
    
    def select_clu(self, selected_clu_list):
        '''
            select clu_id
        '''
        self.select_clus = np.sort(selected_clu_list)
        self.emit('select_clu', action='select_clu')

    @instack_membership
    def reset(self):
        '''reset to 0'''
        self.membership = np.zeros_like(self.membership)
        self.__construct__()
        self.emit('cluster', action='reset')

    @instack_membership
    def merge(self, mergelist):
        '''
        merge two or more clusters, target cluNo is the lowest id
        merge([0,3]): merge clu0 and clu3 to clu0
        merge([2,6,4]): merge clu2,4,6, to clu2
        '''
        clu_to = min(mergelist)
        for cluNo in mergelist:
            self.membership[self.membership==cluNo] = clu_to
        self.__construct__()
        self.emit('cluster', action='merge')

    @instack_membership
    def move(self,clus_from,clu_to):
        '''
          move subsets from clus_from to clu_to, the clus_from is dict which including at least one clu and the subset in this clu,eg:
          clus_from = {1:[1,2,3,4],2:[2,3,4,5]}, move these all spk to the clu_to 1
        '''
        selected_global_idx = self.local2global(clus_from)

        self.membership[selected_global_idx] = clu_to
        self.__construct__()
        
        self.emit('cluster', action = 'move')
        
        return self.global2local(selected_global_idx)[clu_to]

    @instack_membership
    def exchange(self, clus1, clus2):
        '''
            exchange cluster label between clus1 and clus2
        '''
        clus1_idx = self.membership == clus1
        clus2_idx = self.membership == clus2
        self.membership[clus1_idx] = clus2
        self.membership[clus2_idx] = clus1
        
        self.__construct__()
        self.emit('cluster', action = 'exchange')


    @instack_membership
    def delete(self, idx):
        self.membership = np.delete(self.membership, idx)
        self.__construct__()

    @property
    def changed(self):
        if np.array_equal(self._membership_stack[-1], self.membership):
            return False
        else:
            return True
    

    def fill(self, *args, **kwargs):
        '''
        clu_to is the new membership
        clu.fill(global_idx, clu_to) for changing some of the membership
        or
        clu.fill(clu_to) for changing all memberships
        '''
        self._membership_stack.append(self.membership.copy())

        if len(args) == 1:
            global_idx = np.arange(self.npts)
            clu_to     = args[0]
        if len(args) == 2:
            global_idx = args[0]
            clu_to     = args[1]

        if type(clu_to) is int or type(clu_to) is np.int32 or type(clu_to) is np.int64:
            clu_to = [clu_to]*len(global_idx)
        else:
            assert len(global_idx) == len(clu_to)
        #  print 'received fill event, global_idx:{}, clu_to:{}'.format(global_idx, clu_to)
        for idx, clu in zip(global_idx, clu_to):
            self.membership[idx] = clu

        self.__construct__()

        if self.changed:   # prevent those redundant downstream cost (especially connect to many callbacks)
            self.emit('cluster') # , action = 'fill'


    def refill(self, global_idx, labels):
        assert len(global_idx) == len(labels)

        self.membership[global_idx] = labels
        if self.membership.min()>0:
                self.membership -= 1
                
        self.__construct__()
        self.emit('cluster', action = 'refill')

    # FIXME need to a better way to deal with this
    @property
    def max_clu_id(self):
        return max(self.index_id)
   
    # FIXME need to a better way to deal with this
    @property
    def select_clusters(self):
        return self._select_clusters
   
    # FIXME need to a better way to deal with this
    @select_clusters.setter
    def select_clusters(self, clusters):
        self._select_clusters = clusters

    def remove(self, global_ids):
        '''
            remove certain id from clu, and recontruct the cluster. 
            the reason not using @instack_membership because it doest't help a lot when undo, because we not only remove the clu, also remove 
            spk, fet, pivotal. Have no need to support undo until spk, fet, pivotal support undo.
        '''

        self.membership = np.delete(self.membership, global_ids)
        self.__construct__()

        for i in range(len(self._membership_stack)):
            self._membership_stack[i] = np.delete(self._membership_stack[i], global_ids)

    def mask(self, global_ids):
        self.membership = np.delete(self.__membership, global_ids)
        self.__construct__()
    
    @instack_membership
    def split(self, clus_from):
        clu_to = self.index_id.max()+1
        self.move(clus_from, clu_to)


    def undo(self):
        if len(self._membership_stack) > 0:
            self.membership = self._membership_stack.pop()
            self.__construct__()
            self.selectlist = np.array([], np.int64) 
            self.emit('cluster', action = 'undo')
        else:
            debug('no more undo')

    def redo(self):
        # TODO: add redo stack
        pass






# class CLU(Clustering):
#     def __init__(self, clu):
#         super(CLU, self).__init__(clu)
#         self.__init()
        
#     def __init(self):
#         self.spike_id     = {}
#         self.spike_counts = {}
#         self.update()
#         @self.connect
#         def on_cluster(up):
#             self.update()
    

#     def update(self):
#         self.spike_counts = {}
#         self.spike_id     = {}
#         for cluNo in self.cluster_ids:
#             self.spike_id[cluNo]     = self.spikes_in_clusters((cluNo,))
#             self.spike_counts[cluNo] = self.spikes_in_clusters((cluNo,)).shape[0]


#     def __getitem__(self, cluNo):
#         if cluNo in self.cluster_ids:
#             return self.spikes_in_clusters((cluNo,))
#         else:
#             print "out of possible index\ncluster_ids: {}".format(self.cluster_ids)

#     def raster(self, method='vispy', toi=None, color='k'):
#         """
#         Creates a raster plot
#         Parameters
#         ----------
#         toi:    [t0, t1]
#                 time of interest 
#         color : string
#                 color of vlines 
#         Returns
#         -------
#         ax : an axis containing the raster plot
#         """
#         if method == 'matplotlib':
#             fig = plt.figure()
#             ax = plt.gca()
#             for ith, trial in self.spike_id.item():
#                 ax.vlines(trial, ith + .5, ith + 1.5, color=color)
#             ax.set_ylim(.5, len(self.spktime) + .5)
#             ax.set_xlim(toi)
#             ax.set_xlabel('time')
#             ax.set_ylabel('cell')
#             fig.show()
#             return fig, ax
    
#         elif method == 'vispy':
#             rview = raster_view()
#             rview.set_data(timelist=self.spktime, color=(1,1,1,1))
# #             rview.show()
#             return rview

