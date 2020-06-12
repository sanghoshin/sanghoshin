from django.core.exceptions import ObjectDoesNotExist
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.parsers import JSONParser

from edgetron.models import K8sCatalog, Network, Subnet, Port
from edgetron.serializers import K8sCatalogSerializer
from edgetron.sonahandler import SonaHandler
from edgetron.hostmanager import HostManager
from edgetron.ipmanager import IpManager

import uuid, random, subprocess, logging

from edgetron.cluster-api-lib.cluster_api import *

from edgetron.cluster-api-lib.machine import Machine
from edgetron.cluster-api-lib.machineset import MachineSet
from edgetron.cluster-api-lib.cluster import Cluster
from edgetron.cluster-api-lib.network import Network
from edgetron.cluster-api-lib.subnet import Subnet
from edgetron.cluster-api-lib.ipaddress import Ipaddress

sona_ip = "10.2.1.33"
host_list = ["10.2.1.68", "10.2.1.69", "10.2.1.70"]
host_manager = HostManager(host_list)
ip_manager = IpManager("10.10.1", "192.168.0")


@csrf_exempt
def catalog_list(request):
    """
    List all code catalogs, or create a new catalog.
    """
    if request.method == 'GET':
        catalogs = K8sCatalog.objects.all()
        serializer = K8sCatalogSerializer(catalogs, many=True)
        return JsonResponse(serializer.data, safe=False)

    elif request.method == 'POST':
        data = JSONParser().parse(request)
        serializer = K8sCatalogSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return JsonResponse(serializer.data, status=201)
        return JsonResponse(serializer.errors, status=400)


@csrf_exempt
def kubernetes_cluster(request):
    """
    On board the catalog
    """
    sona = SonaHandler(sona_ip)

    if request.method == 'POST':
        data = JSONParser().parse(request)
        serializer = K8sCatalogSerializer(data=data)
        if serializer.is_valid():
            serializer.save()

            cluster_id = serializer.clusterId
            network_id = str(uuid.uuid4())
            segment_id = 1
            tenant_id = str(uuid.uuid4())
            network = Network(clusterId=cluster_id, networkId=network_id, segmentId=segment_id,
                              tenantId=tenant_id)
            network.save()

            r = sona.send_network_request(network_id, segment_id, tenant_id)
            if r.status_code != 201:
                return JsonResponse(r.text, safe=False)

            subnet_id = str(uuid.uuid4())
            cidr = "10.10.1.0/24"
            start = "10.10.1.2"
            end = "10.10.1.255"
            gateway = "10.10.1.1"
            subnet = Subnet(networkId=network_id, subnetId=subnet_id,
                            tenantId=tenant_id, cidr=cidr, startIp=start,
                            endIp=end, gateway=gateway)
            subnet.save()

            r = sona.send_subnet_request(network_id, subnet_id, tenant_id, cidr, start, end, gateway)
            if r.status_code != 201:
                return JsonResponse(r.text, safe=False)

            port_id = str(uuid.uuid4())
            ip_address = "10.10.1.2"
            mac_data = [0x00, 0x16, 0x3e,
                        random.randint(0x00, 0x7f),
                        random.randint(0x00, 0xff),
                        random.randint(0x00, 0xff)]
            mac_address = ':'.join(map(lambda x: "%02x" % x, mac_data))
            port = Port(portId=port_id, subnetId=subnet_id, networkId=network_id,
                        tenantId=tenant_id, ipAddress=ip_address, macAddress=mac_address)
            port.save()

            r = sona.send_createport_request(network_id, subnet_id, port_id, ip_address, tenant_id, mac_address)
            if r.status_code != 201:
                return JsonResponse(r.text, safe=False)

            vcpus = serializer.vcpus
            memory = serializer.memory
            storage = serializer.storage
            host_ip = host_manager.allocate(cluster_id, vcpus, memory, storage)
            k8s_version = serializer.version
            image_name = serializer.image
            vm_ip = ip_manager.allocate_ip(port_id)
            bootstrap_nw_ip = ip_manager.get_bootstrap_nw_ip(port_id)



            # Create a cluster
            cluster = Cluster()
            cluster.withClusterName("mectb") \
                .withPodCidr("10.10.0.0/16") \
                .withServiceCidr("20.20.0.0/24") \
                .withServiceDomain("mectb.io") \
                .withKubeVersion(k8s_version) \
                .withOsDistro(image_name)

            cluster_yaml = create_cluster_yaml(cluster)
            # create_cluster(cluster_yaml)
            print cluster_yaml


        else:
            return JsonResponse(serializer.errors, status=400)

        return JsonResponse(serializer.data, status=200)


@csrf_exempt
def deployment_application(request, cid, chartid):
    """""
    Deploy Application
    """""
    try:
        catalog = K8sCatalog.objects.get(pk=cid)
    except K8sCatalog.DoesNotExist:
        return HttpResponse(status=404)

    if request.method == 'POST':
        data = JSONParser().parse(request)
        serializer = K8sCatalogSerializer(data=data)
        if serializer.is_valid():
            chart_path = get_chart_path(chartid)
            host_ip = host_manager.get_host_ip(cid)
            deploy(host_ip, chart_path)
        else:
            return JsonResponse(serializer.errors, status=400)

        return JsonResponse(serializer.data, status=200)


def get_chart_path(chart_id):
    """ Originally chart path needs to be extracted from DB
        using the chart_id """
    return "bitnami/nginx"


def deploy(host_ip, chart_path):
    command = "helm install my-release " + chart_path

    ssh = subprocess.Popen(["ssh", "%s" % host_ip, command],
                           shell=False,
                           stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    result = ssh.stdout.readlines()
    if not result:
        error = ssh.stderr.readlines()
        logging.debug(error)
    else:
        logging.info(result)