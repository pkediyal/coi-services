#!/usr/bin/env python
'''
@author M Manning
@file ion/processes/data/transforms/test/test_transform_worker.py
'''

from ion.services.dm.utility.granule.record_dictionary import RecordDictionaryTool
from ion.processes.data.ingestion.stream_ingestion_worker import retrieve_stream

import numpy as np

from pyon.ion.stream import StandaloneStreamPublisher, StreamSubscriber, StandaloneStreamSubscriber
from pyon.util.int_test import IonIntegrationTestCase
from pyon.util.file_sys import FileSystem, FS
from pyon.event.event import EventSubscriber
from pyon.public import OT, RT, PRED
from pyon.util.containers import DotDict
from pyon.core.object import IonObjectDeserializer
from pyon.core.bootstrap import get_obj_registry
from pyon.public import log, IonObject
from interface.objects import  DataProcessTypeEnum, DataProcessTypeEnum, TransformFunctionType

from nose.plugins.attrib import attr

from ion.services.dm.utility.granule_utils import SimplexCoverage, ParameterDictionary, GridDomain, ParameterContext
from coverage_model.parameter_types import QuantityType, ArrayType
from coverage_model import NumexprFunction, PythonFunction

from ion.services.dm.utility.granule_utils import time_series_domain
from interface.services.coi.iresource_registry_service import ResourceRegistryServiceClient
from interface.services.dm.idataset_management_service import DatasetManagementServiceClient
from interface.services.dm.ipubsub_management_service import PubsubManagementServiceClient
from interface.services.sa.idata_product_management_service import DataProductManagementServiceClient
from interface.services.sa.idata_process_management_service import DataProcessManagementServiceClient
from interface.services.cei.iprocess_dispatcher_service import ProcessDispatcherServiceClient
from ion.processes.data.transforms.transform_worker import TransformWorker

from coverage_model.coverage import AbstractCoverage

from gevent.event import Event

import unittest
import os


@attr('INT', group='dm')
class TestTransformWorker(IonIntegrationTestCase):

    def setUp(self):
        self._start_container()
        self.container.start_rel_from_url('res/deploy/r2deploy.yml')
        self.dataset_management_client = DatasetManagementServiceClient(node=self.container.node)
        self.pubsub_client = PubsubManagementServiceClient(node=self.container.node)
        self.dataproductclient = DataProductManagementServiceClient(node=self.container.node)
        self.dataprocessclient = DataProcessManagementServiceClient(node=self.container.node)
        self.processdispatchclient = ProcessDispatcherServiceClient(node=self.container.node)
        self.rrclient = ResourceRegistryServiceClient(node=self.container.node)

        self.time_dom, self.spatial_dom = time_series_domain()



    @attr('LOCOINT')
    @unittest.skipIf(os.getenv('CEI_LAUNCH_TEST', False), 'Skip test while in CEI LAUNCH mode')
    def test_transform_worker(self):
        self.loggerpids = []
        self.data_process_objs = []
        self._output_stream_ids = []

        self.start_transform_worker()

        self.parameter_dict_id = self.dataset_management_client.read_parameter_dictionary_by_name(name='ctd_parsed_param_dict', id_only=True)

        # create the StreamDefinition
        self.stream_def_id = self.pubsub_client.create_stream_definition(name='stream_def', parameter_dictionary_id=self.parameter_dict_id)
        self.addCleanup(self.pubsub_client.delete_stream_definition, self.stream_def_id)

        # create the DataProduct
        input_dp_obj = IonObject(  RT.DataProduct, name='input_data_product', description='input test stream',
                                             temporal_domain = self.time_dom.dump(),  spatial_domain = self.spatial_dom.dump())
        self.input_dp_id = self.dataproductclient.create_data_product(data_product=input_dp_obj,  stream_definition_id=self.stream_def_id)

        #retrieve the Stream for this data product
        stream_ids, assoc_ids = self.rrclient.find_objects(self.input_dp_id, PRED.hasStream, RT.Stream, True)
        self.stream_id = stream_ids[0]

        #create a queue to catch the published granules
        self.subscription_id = self.pubsub_client.create_subscription(name='parsed_subscription', stream_ids=[self.stream_id], exchange_name='parsed_subscription')
        self.addCleanup(self.pubsub_client.delete_subscription, self.subscription_id)

        self.pubsub_client.activate_subscription(self.subscription_id)
        self.addCleanup(self.pubsub_client.deactivate_subscription, self.subscription_id)

        stream_route = self.pubsub_client.read_stream_route(self.stream_id)
        self.publisher = StandaloneStreamPublisher(stream_id=self.stream_id, stream_route=stream_route )

        self.start_event_listener()

        self.dp_list = self.create_data_processes()

        self.data_modified = Event()
        self.data_modified.wait(5)

        rdt = RecordDictionaryTool(stream_definition_id=self.stream_def_id)
        rdt['time']         = [0] # time should always come first
        rdt['conductivity'] = [1]
        rdt['pressure']     = [2]
        rdt['salinity']     = [8]

        self.publisher.publish(rdt.to_granule())


        self.data_modified.wait(5)

        # Cleanup processes
        for pid in self.loggerpids:
            self.processdispatchclient.cancel_process(pid)


    def create_data_processes(self):

        #two data processes using one transform and one DPD

        dp1_func_output_dp_id, dp2_func_output_dp_id =  self.create_output_data_products()
        configuration = { 'argument_map':{'arr1':'conductivity', 'arr2':'pressure'}, 'output_param' : 'salinity' }

        # Set up DPD and DP #2 - array add function
        tf_obj = IonObject(RT.TransformFunction,
            name='add_array_func',
            description='adds values in an array',
            function='add_arrays',
            module="ion_example.add_arrays",
            arguments=['arr1', 'arr2'],
            function_type=TransformFunctionType.TRANSFORM

            )
        add_array_func_id, rev = self.rrclient.create(tf_obj)

        dpd_obj = IonObject(RT.DataProcessDefinition,
            name='add_arrays',
            description='adds the values of two arrays',
            data_process_type=DataProcessTypeEnum.TRANSFORM_PROCESS,
            )
        self.add_array_dpd_id = self.dataprocessclient.create_data_process_definition_new(data_process_definition=dpd_obj, function_id=add_array_func_id)
        self.dataprocessclient.assign_stream_definition_to_data_process_definition(self.stream_def_id, self.add_array_dpd_id, binding='add_array_func' )

        # Create the data process
        dp1_data_process_id = self.dataprocessclient.create_data_process_new(data_process_definition_id=self.add_array_dpd_id, in_data_product_ids=[self.input_dp_id],
                                                                             out_data_product_ids=[dp1_func_output_dp_id], configuration=configuration)

        # Create the data process
        dp2_func_data_process_id = self.dataprocessclient.create_data_process_new(data_process_definition_id=self.add_array_dpd_id, in_data_product_ids=[self.input_dp_id],
                                                                                  out_data_product_ids=[dp2_func_output_dp_id], configuration=configuration)

        return [dp1_data_process_id, dp2_func_data_process_id]


    def create_output_data_products(self):

        dp1_outgoing_stream_id = self.pubsub_client.create_stream_definition(name='dp1_stream', parameter_dictionary_id=self.parameter_dict_id)

        dp1_output_dp_obj = IonObject(  RT.DataProduct,
            name='data_process1_data_product',
            description='output of add array func',
            temporal_domain = self.time_dom.dump(),
            spatial_domain = self.spatial_dom.dump())

        dp1_func_output_dp_id = self.dataproductclient.create_data_product(dp1_output_dp_obj,  dp1_outgoing_stream_id)
        self.addCleanup(self.dataproductclient.delete_data_product, dp1_func_output_dp_id)
        # Retrieve the id of the OUTPUT stream from the out Data Product and add to granule logger
        stream_ids, _ = self.rrclient.find_objects(dp1_func_output_dp_id, PRED.hasStream, None, True)
        self._output_stream_ids.append(stream_ids[0])


        dp2_func_outgoing_stream_id = self.pubsub_client.create_stream_definition(name='dp2_stream', parameter_dictionary_id=self.parameter_dict_id)

        dp2_func_output_dp_obj = IonObject(  RT.DataProduct,
            name='data_process2_data_product',
            description='output of add array func',
            temporal_domain = self.time_dom.dump(),
            spatial_domain = self.spatial_dom.dump())

        dp2_func_output_dp_id = self.dataproductclient.create_data_product(dp2_func_output_dp_obj,  dp2_func_outgoing_stream_id)
        self.addCleanup(self.dataproductclient.delete_data_product, dp2_func_output_dp_id)
        # Retrieve the id of the OUTPUT stream from the out Data Product and add to granule logger
        stream_ids, _ = self.rrclient.find_objects(dp2_func_output_dp_id, PRED.hasStream, None, True)
        self._output_stream_ids.append(stream_ids[0])


        subscription_id = self.pubsub_client.create_subscription('validator', data_product_ids=[dp1_func_output_dp_id, dp2_func_output_dp_id])
        self.addCleanup(self.pubsub_client.delete_subscription, subscription_id)

        def on_granule(msg, route, stream_id):
            log.debug('recv_packet stream_id: %s route: %s   msg: %s', stream_id, route, msg)
            self.validate_output_granule(msg, route, stream_id)


        validator = StandaloneStreamSubscriber('validator', callback=on_granule)
        validator.start()
        self.addCleanup(validator.stop)

        self.pubsub_client.activate_subscription(subscription_id)
        self.addCleanup(self.pubsub_client.deactivate_subscription, subscription_id)

        return dp1_func_output_dp_id, dp2_func_output_dp_id


    def validate_event(self, *args, **kwargs):
        """
        This method is a callback function for receiving DataProcessStatusEvent.
        """
        data_process_event = args[0]
        log.debug("DataProcessStatusEvent: %s" ,  str(data_process_event.__dict__))
        self.assertTrue( data_process_event.origin in self.dp_list)


    def validate_output_granule(self, msg, route, stream_id):
        self.assertTrue( stream_id in self._output_stream_ids)

        rdt = RecordDictionaryTool.load_from_granule(msg)
        log.debug('validate_output_granule  rdt: %s', rdt)
        sal_val = rdt['salinity']
        #self.assertTrue( sal_val == 3)
        np.testing.assert_array_equal(sal_val, np.array([3]))

    def start_event_listener(self):

        es = EventSubscriber(event_type=OT.DataProcessStatusEvent, callback=self.validate_event)
        es.start()

        self.addCleanup(es.stop)


    def start_transform_worker(self):
        config = DotDict()
        config.process.queue_name = 'parsed_subscription'

        self.container.spawn_process(
            name='transform_worker',
            module='ion.processes.data.transforms.transform_worker',
            cls='TransformWorker',
            config=config
        )


    def test_download(self):
        egg_url = 'http://sddevrepo.oceanobservatories.org/releases/ion_example-0.1-py2.7.egg'
        egg_path = TransformWorker.download_egg(egg_url)

        import pkg_resources
        pkg_resources.working_set.add_entry(egg_path)

        from ion_example.add_arrays import add_arrays

        a = add_arrays(1,2)
        self.assertEquals(a,3)