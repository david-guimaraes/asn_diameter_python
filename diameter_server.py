"""
Module implementing a Diameter server able to handle requests
and build up a response based on the messages from the requests.

Using the libDiameter library, as well as the dictDiameter .xml dictionary
"""

import thread
from libDiameter import *

# Server host address and port
HOST = "127.0.0.1"
PORT = 3868

BUFFER_SIZE = 4096  # Limit buffer size to detect complete message
MAX_CLIENTS = 5  # Maximum number of clients accepted by the server

result_codes = {
    'DIAMETER_SUCCESS': 2001,
    'DIAMETER_UNABLE_TO_COMPLY': 5012,
}


class DiameterMessage:
    """
    Object encompassing a Diameter Protocol message.

    Contains information available in the Diameter message header, like
    - version
    - flags
    - length
    - command code
    - application Id
    - Hop by Hop ID and End to End info

    The object also contains in a dictionary structure the AVP values,
    using key:value pairs between the attribute-value pairs.
    """

    def __init__(self, request_data):
        self.version = request_data.ver
        self.flags = request_data.flags
        self.length = request_data.len
        self.command_code = request_data.cmd
        self.application_Id = request_data.appId
        self.HobByHop = request_data.HobByHop
        self.EndToEnd = request_data.EndToEnd
        self.hex_msg = request_data.msg
        self.hex_avps = splitMsgAVPs(self.hex_msg)
        self.avps = {}
        # decoding AVP info and setting them up into a dictionary
        for hex_avp in self.hex_avps:
            field, value = decodeAVP(hex_avp)
            self.avps[field] = value


def invalid_request(diameter_request):
    """
    Method used to respond to invalid Diameter request.

    We define an invalid Diameter request by comparing its command code
    with the ones that we have support for.
    """
    logging.warning("Handling invalid request...")
    # Creating message header
    response_header = HDRItem()
    # Set Diameter message's command code
    response_header.cmd = diameter_request.command_code
    # Set Hop-by-Hop and End-to-End
    initializeHops(response_header)

    # Generating response's AVPs
    response_avps = list()
    response_avps.append(
        encodeAVP('Result-Code', result_codes['DIAMETER_UNABLE_TO_COMPLY']))

    # Generating the actual Diameter response
    # by joining the header and the AVPs
    response_message = createRes(response_header, response_avps)
    return response_message


def generate_capability_exchange_answer(diameter_request):
    """
    Method used with the purpose of handling CER requests
    and sending CEA responses.(Capability Exchange)

    Build CEA message, the header and AVPs list separately,
    then create a Diameter response based on them.
    """

    # Creating message header
    cea_header = HDRItem()
    # Set Diameter message's command code
    cea_header.cmd = dictCOMMANDname2code('Capabilities-Exchange')
    # Set Hop-by-Hop and End-to-End
    initializeHops(cea_header)

    # Generating the CEA message's AVPs
    cea_avps = list()
    cea_avps.append(encodeAVP(
        'Origin-Host', diameter_request.avps['Origin-Host']))
    cea_avps.append(encodeAVP(
        'Origin-Realm', diameter_request.avps['Origin-Realm']))
    cea_avps.append(encodeAVP(
        'Result-Code', result_codes['DIAMETER_SUCCESS']))
    cea_avps.append(encodeAVP(
        'Vendor-Id', diameter_request.avps['Vendor-Id']))
    cea_avps.append(encodeAVP(
        'Origin-State-Id', diameter_request.avps['Origin-State-Id']))
    cea_avps.append(encodeAVP(
        'Supported-Vendor-Id', diameter_request.avps['Supported-Vendor-Id']))
    cea_avps.append(encodeAVP(
        'Acct-Application-Id', diameter_request.avps['Acct-Application-Id']))

    # Create the Diameter message by joining the header and the AVPs
    cea_message = createRes(cea_header, cea_avps)
    return cea_message


def handle_request(connection, address):
    """
    Method used to handle the incoming requests from the server.
    Represents one of the processing threads,
    for a specific request sent by a peer.

    Tries to receive data from the socket connection based on the buffer,
    and if the buffer is full, then re-tries to add data from the next
    buffered packet, since it may be related to the first one.

    Once the data receiving process is finished, the information is being
    sent for further analysis and building up a response for it.

    :param connection: socket connection on which we handle the request
    :param address: address of the peer requesting the connection
    """
    while True:
        # get input ,wait if no data
        try:
            data = connection.recv(BUFFER_SIZE)
        except socket.error:
            break
        # suspect more data (try to get it all without stopping if no data)
        if len(data) == BUFFER_SIZE:
            while 1:
                try:
                    data += connection.recv(BUFFER_SIZE)
                except socket.error:
                    # error means no more data
                    break
        # no data found exit loop (possible closed socket)
        if len(data) == 0:
            break
        else:
            # actual handling of the Diameter message
            logging.warning("Handling request for address " + str(address))
            message_sent = send_response(connection, data)
            logging.warning(
                "\n\nSent " + str(dictCOMMANDcode2name(message_sent.flags, message_sent.command_code))
                + "\nAVPs:\n" + str(message_sent.avps) + "\n\n")
    connection.close()


def send_response(socket_connection, request_info):
    """
    Method used to send a Diameter response to a request for the server.

    Creates a DiameterMessage object based on the encoded data
    received from the peer, prepares a specific response
    based on the request's Diameter command code, which is then
    being sent over the same socket connection back to the peer
    that performed the request.

    :param socket_connection: peer connection to the server
    :param request_info: data received from the socket connection
    :return: a DiameterMessage object containing the response sent
    """
    # Creating a diameter request message based on the data received
    request = HDRItem()
    stripHdr(request, request_info.encode("hex"))
    diameter_request = DiameterMessage(request)

    cmd_code_responses = {
        # Capabilities Exchange Request
        257: generate_capability_exchange_answer
    }

    # Generating a response based on the request info and command code
    response = cmd_code_responses.get(
        diameter_request.command_code, invalid_request)(diameter_request)

    # Sending the message and returning a Diameter Message object
    socket_connection.send(response.decode('hex'))
    response_data = HDRItem()
    stripHdr(response_data, response)
    diameter_sent_message = DiameterMessage(response_data)
    return diameter_sent_message


def start_diameter_server():
    """
    Main function containing the Diameter Server implementation.

    Loads the Diameter dictionary, enables a socket connection
    listening on the pre-defined port, and then starts a processing thread
    which handles the possible requests for each of the incoming connections
    on said socket from any potential peers.
    """

    # Loading the Diameter dictionary for messages, codes and AVPs
    LoadDictionary("dictDiameter.xml")

    # Create the server, binding to HOST:PORT and set max peers
    diameter_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    diameter_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    diameter_server.bind((HOST, PORT))
    diameter_server.listen(MAX_CLIENTS)

    try:
        while True:
            incoming_connection, peer_address = diameter_server.accept()
            logging.warning('Connected to ' + str(peer_address))
            thread.start_new(
                handle_request, (incoming_connection, peer_address))
    except KeyboardInterrupt:
        print "Closing server..."

    # Closing the socket connection
    diameter_server.close()


if __name__ == "__main__":
    start_diameter_server()
