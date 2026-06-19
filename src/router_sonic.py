
import os
import time
import logging
import textfsm
from netmiko import ConnectHandler
from netmiko import file_transfer

import utility
from router_base import Router_Base

log = logging.getLogger(__name__)


class Router_Sonic(Router_Base):

    def __init__(self, host, username=None, password=None, port=22, ssh_config_file=None):

        super().__init__(host, username, password, port, ssh_config_file)


    def connect(self):

        sonic_vs = {
            'device_type'         : 'linux',
            'host'                : self.host,
            'username'            : self.username,
            'password'            : self.password,
            'port'                : self.port,
            "global_delay_factor" : 3,
            "fast_cli"            : False,
            "ssh_config_file"     : self.ssh_config_file
        }

        try:
            self.router_connect = ConnectHandler(**sonic_vs)
        except Exception as e:
            return False, str(e)

        return True, None


    def get_mgmt_ip(self, interface="eth0"):

        status, output = self.get_interface_info(interface)
        if not status:
            return False, output

        IP = output.get("Ip", None)
        if not IP:
            return False, f"No IP address assigned to {interface}"

        return True, IP


    def get_interface_info(self, interface):

        status, output = self.run_command(f"ip addr show {interface}")
        if not status:
            return False, output

        script_dir = os.path.dirname(os.path.abspath(__file__))
        textfsm_path = os.path.join(script_dir, "textfsm", "ip_address_show.textfsm")
        with open(textfsm_path) as template_file:
            fsm = textfsm.TextFSM(template_file)
            parsed_output = fsm.ParseText(output)

        result = [
            dict(zip(fsm.header, row)) for row in parsed_output
        ]

        if not result:
            return False, f"cannot find interface {interface} info"

        return True, result[0]


    def get_default_gw(self):

        cmd = "ip route show default | awk '/default/ {print $3}'"
        status, output = self.run_command(cmd)
        if not status:
            return False, output

        return True, output.strip()


    #######################################
    ########### Packet Sniffing ###########
    #######################################

    def start_sniffer(self,
                      interface,
                      collector_ip,
                      collector_port,
                      duration=None,
                      count=0,
                      s_filter=None):

        script_file = os.path.realpath(__file__)
        script_file = os.path.abspath(script_file)
        script_dir = os.path.dirname(script_file)

        sniffer_file = os.path.join(script_dir, "sniffer_local.py")
        if not os.path.exists(sniffer_file):
            return False, "cannot find sniffer_local.py script"

        remote_dir = "/tmp"
        sniffer_filename = os.path.basename(sniffer_file)

        try:
            transfer_result = file_transfer(
                self.router_connect,
                source_file=sniffer_file,
                dest_file=sniffer_filename,
                direction='put',
                file_system=remote_dir,
                overwrite_file=True)
        except Exception as e:
            return False, str(e)

        #######

        sniffer_remote_file = os.path.join(remote_dir, sniffer_filename)

        number = utility.get_randint(5)
        server_log_file = f"sniffer_{number}.log"

        cmd = f"cd {remote_dir} &&"
        cmd += f" sudo python3 -u {sniffer_remote_file}"
        cmd += f" --interface {interface}"

        cmd += " --stream_packets"
        cmd += f" --collector_ip {collector_ip}"
        cmd += f" --collector_port {collector_port}"

        if duration is not None:
            cmd += f" --timeout {duration}"

        if count:
            cmd += f" --count {count}"

        if s_filter:
            cmd += f" --filter '{s_filter}'"

        cmd += f" > {server_log_file} 2>&1 &"

        #######

        self.run_command(cmd, check_return_code=False)

        # waiting for the background process
        time.sleep(3)

        cmd_c = f"cat {remote_dir}/{server_log_file}"
        _, output = self.run_command(cmd_c)

        log.info(" Sniffer logs: %s", server_log_file)

        utility.print_list(output.splitlines(), prepend=" ")

        stdout = output.lower()

        if "error" in stdout or "not valid" in stdout:
            return False, "start_sniffer failed"

        return True, None


    ###################
    ###### Sflow ######
    ###################

    def configure_sflow(self,
                        agent_id="Ethernet0",
                        collector_name="collector1",
                        collector_ip=None,
                        collector_port=6343,
                        disabled_interfaces=None,
                        sample_rate_interfaces=None,
                        polling_interval=20, # seconds
                        sample_direction="both"):

        status, output = self.is_service_active("sflow")
        if not status:
            status, output = self.enable_sflow_service()
            if not status:
                return False, output

        status, output = self.is_container_running("sflow")
        if not status:
            return False, output

        # The agent ID is typically an interface that can represent the device in sFlow exports.
        # It's common to use a management interface or any interface that has a stable IP address.
        status, output = self.run_command(f'sudo config sflow agent-id add {agent_id}')
        if not status:
            return False, output

        if not collector_ip:

            # if collector_ip is not specified, then we send out
            # sflow to the default gw

            status, output = self.get_default_gw()
            if not status:
                return False, "failed to get default GW"

            collector_ip = output

        cmd = f'sudo config sflow collector add {collector_name} {collector_ip} --port {collector_port}'
        status, output = self.run_command(cmd)
        if not status:
            return False, output

        # Enabling sflow on all interfaces.
        cmd = f'sudo config sflow interface enable all'
        status, output = self.run_command(cmd)
        if not status:
            return False, output

        # sflow will be disabled for interfaces listed in 'disabled_interfaces'
        if disabled_interfaces:

            if not isinstance(disabled_interfaces, list):
                disabled_interfaces = [disabled_interfaces]

            for intf_name in disabled_interfaces:

                log.info("Disabling sflow on interface %s", intf_name)

                status, output = self.run_command(f'sudo config sflow interface disable {intf_name}')
                if not status:
                    return False, output

        # The default sample rate for any interface is (ifSpeed / 1e6) where ifSpeed is in bits/sec.
        # For example, sample rate for a 1 Gbps interface is 1-in-1000.
        # This default is chosen to allow the detection of a new flow of 10% link bandwidth
        # in under 1 second. It is recommended not to change the defaults.

        if sample_rate_interfaces:

            if not isinstance(sample_rate_interfaces, dict):
                return False, "sample_rate_interfaces should be of type dict"

            for intf_name, intf_speed in sample_rate_interfaces.items():

                status, output = self.run_command(f'sudo config sflow interface sample-rate {intf_name} {intf_speed}')
                if not status:
                    return False, output

        # The polling interval is the frequency at which sFlow agents collect and send
        # counter data to an sFlow collector. The counter polling interval for all
        # interfaces in the range of 5-300 seconds. Zero means disable

        status, output = self.run_command(f'sudo config sflow polling-interval {polling_interval}')
        if not status:
            return False, output

        status, output = self.run_command(f'sudo config sflow sample-direction {sample_direction}')
        if not status:
            return False, output

        ########

        # enabling sflow

        status, output = self.run_command('sudo config sflow enable')
        if not status:
            return False, output

        ########

        status, output = self.run_command('show sflow')
        if not status:
            return False, output

        utility.print_list(output.splitlines(), prepend=" "*2)

        ########

        status, output = self.run_command('show sflow interface')
        if not status:
            return False, output

        utility.print_list(output.splitlines(), prepend=" "*2)

        ########

        return True, None


    def get_service_details(self, service_name):

        cmd = f"sudo systemctl show --no-pager {service_name}"
        status, output = self.run_command(cmd)
        if not status:
            log.warning(output)
            return {} # do not exit on error

        service_dict = {}

        for line in output.splitlines():

            line_split = line.split("=", 1)
            if len(line_split) != 2:
                continue

            service_dict[line_split[0]] = line_split[1]

        return True, service_dict


    def is_service_active(self, service_name):

        status, output = self.get_service_details(service_name)
        if not status:
            return False, output

        active_state = output.get("ActiveState", None)
        if not active_state:
            return False, "cannot find the active state of the service"

        if active_state == "active":
            return True, None

        return False, "service is not active"


    def enable_sflow_service(self):

        status, output = self.get_service_details("sflow")
        if not status:
            return False, output

        ######

        active_state = output.get("ActiveState", None)
        if not active_state:
            return False, "cannot find the active state of the sflow service"

        # the service is currently running
        if active_state == "active":
            return True, None

        ######

        load_state = output.get("LoadState", None)
        if not load_state:
            return False, "cannot find the state of the sflow service"

        if load_state == "masked":

            status, output = self.run_command('sudo systemctl unmask sflow')
            if not status:
                return False, output

        # configuring the service to start automatically at boot,
        # but doesn't start the service immediately.
        status, output = self.run_command('sudo systemctl enable sflow')
        if not status:
            return False, output

        # starting the service right now
        status, output = self.run_command('sudo systemctl start sflow')
        if not status:
            return False, output

        ######

        # make sure the sflow service is active now

        status, output = self.is_service_active("sflow")
        if not status:
            return False, output

        return True, None


    def is_container_running(self, container_name):

        cmd = f"docker inspect -f '{{{{.State.Running}}}}' {container_name}"

        status, output = self.run_command(cmd)
        if not status:
            return False, output

        if output.strip() != "true":
            return False, f"container {container_name} is not running"

        return True, None
