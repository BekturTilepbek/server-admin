import paramiko

def execute_command(server_data, command):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=server_data['ip'],
            username=server_data['user'],
            password=server_data['password'],
            timeout=10
        )
        stdin, stdout, stderr = client.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode('utf-8')
        err = stderr.read().decode('utf-8')
        return exit_status, out, err
    except Exception as e:
        return -1, "", str(e)
    finally:
        client.close()