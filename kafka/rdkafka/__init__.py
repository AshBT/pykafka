import logging
from time import sleep

from kafka import base
from .config import default_config

try:
    import rd_kafka
except ImportError:
    pass # not installed

logger = logging.getLogger(__name__)


class Cluster(base.BaseCluster):
    def __init__(self, seed_hosts):
        self.config = {"metadata.broker.list": seed_hosts}
        # TODO bind a log_cb to this config ^^
        self._brokers = {}
        self._topics = TopicDict(self)
        self.update()

        # Enable topic auto-creation:

    @property
    def brokers(self):
        return self._brokers

    @property
    def topics(self):
        return self._topics

    def update(self):
        self.meta = rd_kafka.Producer(self.config).metadata()

        self._refresh_no_clobber(self.brokers, self.meta["brokers"], Broker)
        self._refresh_no_clobber(self.topics, self.meta["topics"], Topic)
        for topic in self.topics.values():
            self._refresh_no_clobber(
                    topic.partitions,
                    self.meta["topics"][topic.name]["partitions"],
                    Partition,
                    parent=topic)

    def _refresh_no_clobber(self, target, updates, class_, parent=None):
        """
        Add and remove, but do not update existing elements

        As per the spec in kafka.base, this avoids replacing elements
        that may be referenced in other places
        """
        removed = set(target.keys()) - set(updates.keys())
        for key in removed:
            logger.info("Removing {}".format(target[key]))
            del target[key]
        added = set(updates.keys()) - set(target.keys())
        for key in added:
            logger.info("Adding {}".format(updates[key]))
            target[key] = class_(parent or self, key)


class Broker(base.BaseBroker):
    """ Just an adapter class for rd_kafka broker metadata """

    def __init__(self, cluster, broker_id):
        self.cluster = cluster
        self._id = broker_id

    @property
    def id(self):
        return self._id

    @property
    def host(self):
        return self.cluster.meta["brokers"][self.id]["host"]

    @property
    def port(self):
        return self.cluster.meta["brokers"][self.id]["port"]


class Topic(base.BaseTopic):

    def __init__(self, cluster, name):
        self.cluster = cluster
        self._name = name
        self._partitions = {}

    @property
    def name(self):
        return self._name

    @property
    def partitions(self):
        return self._partitions

    def latest_offsets(self):
        raise NotImplementedError # TODO

    def earliest_offsets(self):
        raise NotImplementedError # TODO


class TopicDict(dict):
    """ A dict that knows how to create Topics """

    def __init__(self, cluster, *args, **kwargs):
        super(TopicDict, self).__init__(*args, **kwargs)
        self.cluster = cluster

    def __missing__(self, key):
        logger.info("Trying to create topic '{}'".format(key))

        # To do a metadata request on a non-existing topic, librdkafka
        # expects you to create an actual topic handle, so:
        rd_kafka.Producer(self.cluster.config).open_topic(key).metadata()

        # Now update self.cluster, which in turn updates this dict.  Because
        # creating topic-partitions and assigning them leaders and all takes
        # time, we might need a few rounds of refreshes.  To avoid introducing
        # more settings, we reuse existing ones which have roughly the right
        # meaning:
        conf = default_config()
        for i in range(int(conf["topic.metadata.refresh.fast.cnt"])):
            sleep(float(
                    conf["topic.metadata.refresh.fast.interval.ms"]) * 1e-3)
            self.cluster.update()
            # This is empiricism at work: the topic might appear in metadata on
            # one run, but only have partitions associated with it on the next,
            # so test both:
            if key in self and self[key].partitions:
                break
        else:
            raise KeyError(key)
        # TODO can we detect/report if auto-creation is disabled in kafka?


class Partition(object):

    def __init__(self, topic, partition_id):
        self._topic = topic
        self._id = partition_id

    def _meta(self):
        """ Helper that points into the rd_kafka metadata dict """
        meta, t_name = self.topic.cluster.meta, self.topic.name
        return meta["topics"][t_name]["partitions"][self.id]

    @property
    def id(self):
        return self._id

    @property
    def leader(self):
        leader_id = self._meta()["leader"]
        return self.topic.cluster.brokers[leader_id]

    @property
    def replicas(self):
        replica_ids = self._meta()["replicas"]
        return [self.topic.cluster.brokers[i] for i in replica_ids]

    @property
    def isr(self):
        isr_ids = self._meta()["isrs"]
        return [self.topic.cluster.brokers[i] for i in isr_ids]

    @property
    def topic(self):
        return self._topic

    def latest_offset(self):
        raise NotImplementedError # TODO

    def earliest_offset(self):
        raise NotImplementedError # TODO
