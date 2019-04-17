from queue import PriorityQueue
import gsim_utils as gu
from modules import Queue, Server, AnomalyDetector
from generators import Source
from datetime import datetime
from events import Event
import logging.config
import pandas as pd
import numpy as np
import logging
import os

logging.config.fileConfig('logging.conf')
logger = logging.getLogger('simulation')


class Simulation:

    def __init__(self, duration, model=None):
        self.duration = duration
        self.model = model
        self.pq = PriorityQueue()
        self.time = 0

    def get_summary(self, add_to_log=True):

        if add_to_log:
            logger.info("*** Summary for model %s ***" % self.model.name)
            logger.info("- " * 30)

        vector_res = {
            'module_name': [],
            'module_class': [],
            'module_id': [],
            'packet_id': [],
            'arrival_time': [],
            'departure_time': [],
            'malicious': []
        }

        scalar_res = {
            'module_name': [],
            'module_class': [],
            'module_id': [],
            'total_arrivals': [],
            'total_departures': [],
            'normal_arrivals': [],
            'normal_departures': [],
            'attack_arrivals': [],
            'attack_departures': []
        }

        for module in self.model.get_modules():
            if add_to_log:
                logger.info('Module: %s' % module.name)
                logger.info('-' * 30)

            if hasattr(module, 'results'):
                module_vector_res = module.results.get_vector_results(add_to_log=add_to_log)
                vector_res['packet_id'].extend(module_vector_res['packet_id'])
                vector_res['arrival_time'].extend(module_vector_res['arrival_time'])
                vector_res['departure_time'].extend(module_vector_res['departure_time'])
                vector_res['malicious'].extend(module_vector_res['malicious'])
                vector_res['module_class'].extend([module.__class__] * len(module_vector_res.get('packet_id')))
                vector_res['module_name'].extend([module.name] * len(module_vector_res.get('packet_id')))
                vector_res['module_id'].extend([id(module)] * len(module_vector_res.get('packet_id')))

                module_scalar_res = module.results.get_scalar_results()
                scalar_res['module_name'].append(module.name)
                scalar_res['module_id'].append(id(module))
                scalar_res['module_class'].append(module.__class__)
                scalar_res['total_arrivals'].append(module_scalar_res[0])
                scalar_res['total_departures'].append(module_scalar_res[1])
                scalar_res['normal_arrivals'].append(module_scalar_res[2])
                scalar_res['normal_departures'].append(module_scalar_res[3])
                scalar_res['attack_arrivals'].append(module_scalar_res[4])
                scalar_res['attack_departures'].append(module_scalar_res[5])

            if add_to_log:
                logger.info("- " * 30)

        # Create Dataframes for results
        df_vector = pd.DataFrame(vector_res, columns=list(vector_res.keys()))
        df_scalar = pd.DataFrame(scalar_res, columns=list(scalar_res.keys()))

        # Save results to csv
        if not os.path.isdir('results'):
            os.mkdir('results')
        datenum = datetime.now().strftime("%y%m%d-%H%M%S")
        df_vector.to_csv('results/vec-%s.csv' % datenum)
        df_scalar.to_csv('results/sca-%s.csv' % datenum)

    def process_event(self, event):

        # Each event has a module and packet associated with it
        module_id = event.get_module_id()
        packet_id = event.get_packet_id()
        module_ = self.model.get_module(module_id)
        packet = self.model.get_packet(packet_id)

        if module_ is None:
            raise ValueError("Module with id %s not found! Check if all modules are registered with model using "
                             "`model.add_module()`." % module_id)

        if event.etype == 'SERVICE_COMPLETE':

            logger.info("%8.3f -- %s at node %s, packet id: %s" %
                        (self.get_time(), event.etype, module_.name, str(packet_id)))
            del event
            module_.busy = False

            # Send to one of the module's outputs - at least one of the outputs must be a Queue!!
            destination = gu.choose_output(module_.outputs)
            if destination is None:
                # This should never happen
                logger.error("%8.3f -- node %s, packet id: %s - Destination not found!" %
                             (self.get_time(), module_.name, str(packet_id)))
                raise TypeError("Destination not found in outputs of node %s. Make sure there's a Queue as output." %
                                module_.name)

            event = gu.create_event(destination, self.get_time(), packet_id)
            self.add_event(event)

            # Get a new packet from inputs
            input_module = module_.inputs[0]['module']
            # todo: Implement multiple inputs to a server

            if type(input_module) == Queue:
                logger.debug("%8.3f -- Server %s asks input %s (len: %d) for more packets" %
                             (self.get_time(), module_.name, input_module.name, len(input_module)))

            if type(input_module) == Queue and len(input_module) > 0:
                new_packet = input_module.pop()
                event = gu.create_event(module_, self.get_time(), id(new_packet))
                self.add_event(event)

        elif event.etype == 'DETECTOR_SERVICE_COMPLETE':

            # Make a decision on malicious packet detection
            if packet.is_malicious():
                detect_prob = module_.tp_rate
            else:
                detect_prob = module_.fp_rate

            decision_attack = np.random.choice([True, False], 1, p=[detect_prob, 1-detect_prob])[0]
            packet.detected = decision_attack

            logger.info("%8.3f -- %s at node %s, packet id: %s, detected attack: %s" %
                        (self.get_time(), event.etype, module_.name, str(packet_id), str(decision_attack)))

            del event
            module_.busy = False

            if decision_attack:
                destination = gu.choose_output(module_.outputs_detected)
            else:
                destination = gu.choose_output(module_.outputs)
            if destination is None:
                # This should never happen
                logger.error("%8.3f -- node %s, packet id: %s - Destination not found!" %
                             (self.get_time(), module_.name, str(packet_id)))
                raise TypeError("Destination not found in outputs of node %s. Make sure there's a Queue as output." %
                                module_.name)

            event = gu.create_event(destination, self.get_time(), packet_id)
            self.add_event(event)

            # Get a new packet from inputs (if queue)
            input_module = module_.inputs[0]['module']
            # todo: Implement multiple inputs to a server

            if type(input_module) == Queue:
                logger.debug("%8.3f -- AnomalyDetector %s asks input %s (len: %d) for more packets" %
                             (self.get_time(), module_.name, input_module.name, len(input_module)))

            if type(input_module) == Queue and len(input_module) > 0:
                new_packet = input_module.pop()
                event = gu.create_event(module_, self.get_time(), id(new_packet))
                self.add_event(event)

        elif event.etype == 'PACKET_GENERATION':
            # generate a new packet
            logger.info("%8.3f -- %s at node %s, packet id: %s" %
                        (self.get_time(), event.etype, module_.name, str(packet_id)))
            module_.generate_packet()
            del event

        elif event.etype == 'QUEUE_PACKET_ARRIVAL':
            # A packet has arrived in a queue
            packet.set_module(module_id)
            module_.appendleft(packet)
            logger.info("%8.3f -- %s at node %s (qlen: %d), packet id: %s" %
                        (self.get_time(), event.etype, module_.name, len(module_), str(packet_id)))
            del event

            # if packet is first in the queue, inform the output module of this
            if len(module_) == 1:
                destination = gu.choose_output(module_.outputs)
                if destination:
                    # Forward packet to destination if not busy, otherwise do nothing
                    event = gu.create_event(destination, self.get_time(), packet_id)
                    self.add_event(event)
                    module_.pop()

        elif event.etype == 'SERVER_PACKET_ARRIVAL' or event.etype == 'DETECTOR_PACKET_ARRIVAL':
            # A packet has arrived in a server or anomaly detector
            packet.set_module(module_id)
            if not module_.busy:
                module_.busy = True

            # Schedule service end time
            service_duration = np.random.exponential(1 / module_.service_rate)

            logger.info("%8.3f -- %s at node %s, packet id: %s, service duration: %.3f" %
                        (self.get_time(), event.etype, module_.name, str(packet_id), service_duration))

            timestamp = service_duration + self.get_time()
            etype = 'SERVICE_COMPLETE'
            if event.etype == 'DETECTOR_PACKET_ARRIVAL':
                etype = 'DETECTOR_SERVICE_COMPLETE'
            del event

            event = Event(
                timestamp=timestamp,
                etype=etype,
                module_id=module_id,
                packet_id=packet_id
            )
            self.add_event(event)

    def add_model(self, model):
        self.model = model
        for module in model.get_modules():
            module.register_with_sim(self)
        for source in model.get_sources():
            source.register_with_sim(self)

    def get_model(self):
        return self.model

    def get_time(self):
        return self.time

    def add_event(self, event):
        self.pq.put(event)

    def get_duration(self):
        return self.duration

    def run(self):

        print("Simulation running..")

        # Start data generators
        model = self.model
        for _, source in model.sources.items():
            source.generate_packet()

        while self.pq.qsize() > 0:

            # Get next event from queue
            event = self.pq.get()
            self.time = event.get_timestamp()

            # Check if simulation end reached
            if self.time > self.duration:
                break

            # Process event
            self.process_event(event)

        # End of simulation
        self.get_summary()


class Model:

    def __init__(self, name=None):
        self.modules = {}
        self.packets = {}  # todo: check if I really need this
        self.sources = {}
        self.destinations = {}
        self.name = name

    def add_module(self, module):
        module.register_with_model(self)
        self.modules[id(module)] = module

        if type(module) == Source:
            self.sources[id(module)] = module

    def get_module(self, module_id):
        return self.modules.get(module_id)

    def get_modules(self):
        return list(self.modules.values())

    def get_sources(self):
        return list(self.sources.values())

    def add_packet(self, packet):
        self.packets[id(packet)] = packet

    def get_packet(self, packet_id):
        return self.packets.get(packet_id, None)  # todo check if works like this


if __name__ == '__main__':

    sim = Simulation(duration=2000)
    m = Model(name='model1')

    # Declare model's components
    q1 = Queue(name='q1')
    s1 = Server(name='s1', service_rate=0.2)
    q2 = Queue(name='q2')
    ad = AnomalyDetector(name='detector1', service_rate=0.2, tp_rate=0.9, fp_rate=0.05)
    q_nor = Queue(name='dest_normal')
    q_att = Queue(name='dest_attack')
    gen = Source(rate=5, attack_prob=0.3, name='gen')

    # Add component's inputs and outputs
    gen.outputs = [{'module': q1, 'prob': 1}]
    q1.outputs = [{'module': s1, 'prob': 1}]
    s1.inputs = [{'module': q1, 'prob': 1}]
    s1.outputs = [{'module': q2, 'prob': 1}]
    q2.outputs = [{'module': ad, 'prob': 1}]
    ad.inputs = [{'module': q2, 'prob': 1}]
    ad.outputs = [{'module': q_nor, 'prob': 1}]
    ad.outputs_detected = [{'module': q_att, 'prob': 1}]

    # Register components to model
    m = Model()
    m.add_module(gen)
    m.add_module(q1)
    m.add_module(s1)
    m.add_module(q2)
    m.add_module(ad)
    m.add_module(q_nor)
    m.add_module(q_att)

    # Register model with simulation.
    # This also registers all model's modules with simulation.
    sim.add_model(m)
    sim.run()
