
import logging

log = logging.getLogger(__name__)


class Router_Base():

    def __init__(self, host, username, password, port=22, ssh_config_file=None):

        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.ssh_config_file = ssh_config_file

        self.terminate = False
        self.router_connect = None


    def stop(self):

        self.terminate = True


    def run_command(self, cmd, check_return_code=True):

        if not check_return_code:

            try:
                output = self.router_connect.send_command(cmd)
                return True, output
            except Exception as e:
                return False, str(e)

        cmd = f"{cmd}; echo $?"

        try:

            output = self.router_connect.send_command(cmd)

            lines = output.strip().splitlines()
            exit_code = int(lines[-1])
            command_output = "\n".join(lines[:-1])

            if exit_code != 0:
                return False, command_output

            return True, command_output

        except Exception as e:
            return False, str(e)
