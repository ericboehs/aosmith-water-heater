// Headless CTA-2045 bridge: one command per line in on stdin, one JSON object
// per line out on stdout.
//
// Everything the tank says comes out of the same stdout stream -- solicited
// responses, spontaneous operational-state pushes, and customer-override
// notices alike -- so the supervisor never has to correlate a reply with the
// request that caused it. It just reacts to whatever arrives.
//
// easylogging is reconfigured to a file before anything else happens. The
// library logs on its own initiative from the receive thread, and a single
// stray line on stdout would desync the JSON stream.

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "easylogging++.h"

#include <cea2045/communicationport/CEA2045SerialPort.h>
#include <cea2045/device/DeviceFactory.h>

INITIALIZE_EASYLOGGINGPP

using namespace cea2045;

namespace {

std::mutex g_outMutex;

void emit(const std::string &json)
{
	std::lock_guard<std::mutex> lock(g_outMutex);
	std::fputs(json.c_str(), stdout);
	std::fputc('\n', stdout);
	std::fflush(stdout);
}

std::string quote(const std::string &in)
{
	std::string out = "\"";
	for (char c : in)
	{
		switch (c)
		{
		case '"':  out += "\\\""; break;
		case '\\': out += "\\\\"; break;
		case '\n': out += "\\n";  break;
		case '\r': out += "\\r";  break;
		case '\t': out += "\\t";  break;
		default:
			if ((unsigned char)c < 0x20)
			{
				char esc[8];
				snprintf(esc, sizeof(esc), "\\u%04x", c);
				out += esc;
			}
			else
				out += c;
		}
	}
	return out + "\"";
}

// Fixed-width CTA-2045 text fields are padded with spaces or NULs.
std::string trimmedField(const unsigned char *buf, size_t len)
{
	std::string s((const char *)buf, len);
	size_t end = s.find_last_not_of(" \t\0", std::string::npos);
	if (end == std::string::npos)
		return "";
	return s.substr(0, end + 1);
}

std::string hexDump(const unsigned char *buf, size_t len)
{
	std::string out;
	char tmp[4];
	for (size_t i = 0; i < len; i++)
	{
		snprintf(tmp, sizeof(tmp), "%02X", buf[i]);
		if (i) out += ' ';
		out += tmp;
	}
	return out;
}

const char *responseCodeName(ResponseCode code)
{
	switch (code)
	{
	case ResponseCode::OK:               return "ok";
	case ResponseCode::TIMEOUT:          return "timeout";
	case ResponseCode::BAD_CRC:          return "bad_crc";
	case ResponseCode::INVALID_RESPONSE: return "invalid_response";
	case ResponseCode::NO_ACK_RECEIVED:  return "no_ack";
	case ResponseCode::NAK:              return "nak";
	}
	return "unknown";
}

const char *nakCodeName(LinkLayerNakCode nak)
{
	switch (nak)
	{
	case LinkLayerNakCode::NO_REASON:                return "no_reason";
	case LinkLayerNakCode::INVALID_BYTE:             return "invalid_byte";
	case LinkLayerNakCode::INVALID_LENGTH:           return "invalid_length";
	case LinkLayerNakCode::CHECKSUM_ERROR:           return "checksum_error";
	case LinkLayerNakCode::RESERVED:                 return "reserved";
	case LinkLayerNakCode::MESSAGE_TIMEOUT:          return "message_timeout";
	case LinkLayerNakCode::UNSUPPORTED_MESSAGE_TYPE: return "unsupported_message_type";
	case LinkLayerNakCode::REQUEST_NOT_SUPPORTED:    return "request_not_supported";
	case LinkLayerNakCode::NONE:                     return "none";
	}
	return "unknown";
}

class JsonUCM : public IUCM
{
public:
	bool isMessageTypeSupported(MessageTypeCode messageType) override
	{
		return messageType != MessageTypeCode::NONE;
	}

	MaxPayloadLengthCode getMaxPayload() override
	{
		return MaxPayloadLengthCode::LENGTH4096;
	}

	void processMaxPayloadResponse(MaxPayloadLengthCode maxPayload) override
	{
		std::ostringstream o;
		o << "{\"t\":\"max_payload\",\"code\":" << (int)maxPayload << "}";
		emit(o.str());
	}

	void processDeviceInfoResponse(cea2045DeviceInfoResponse *m) override
	{
		char version[3] = { (char)m->version[0], (char)m->version[1], 0 };
		char revision[16];
		snprintf(revision, sizeof(revision), "%d.%d",
		         (int)m->deviceRevision[0], (int)m->deviceRevision[1]);
		char firmware[32];
		snprintf(firmware, sizeof(firmware), "%d.%d (%04d-%02d-%02d)",
		         (int)m->firmwareMajor, (int)m->firmwareMinor,
		         2000 + (int)m->firmwareYear20xx, (int)m->firmwareMonth,
		         (int)m->firmwareDay);

		unsigned int capability = ((unsigned int)m->capability[0] << 24)
		                        | ((unsigned int)m->capability[1] << 16)
		                        | ((unsigned int)m->capability[2] << 8)
		                        |  (unsigned int)m->capability[3];

		std::ostringstream o;
		o << "{\"t\":\"device_info\""
		  << ",\"rc\":" << (int)m->responseCode
		  << ",\"device_type\":" << m->getDeviceType()
		  << ",\"vendor_id\":" << m->getVendorID()
		  << ",\"cta_version\":" << quote(version)
		  << ",\"device_revision\":" << quote(revision)
		  << ",\"firmware\":" << quote(firmware)
		  << ",\"capability\":" << capability
		  << ",\"model\":" << quote(trimmedField(m->modelNumber, sizeof(m->modelNumber)))
		  << ",\"serial\":" << quote(trimmedField(m->serialNumber, sizeof(m->serialNumber)))
		  << "}";
		emit(o.str());
	}

	void processCommodityResponse(cea2045CommodityResponse *m) override
	{
		std::ostringstream o;
		o << "{\"t\":\"commodity\",\"rc\":" << (int)m->responseCode << ",\"items\":[";
		int count = m->getCommodityDataCount();
		for (int i = 0; i < count; i++)
		{
			cea2045CommodityData *d = m->getCommodityData(i);
			if (d == nullptr)
				break;
			if (i)
				o << ',';
			o << "{\"code\":" << (int)d->commodityCode
			  << ",\"cumulative\":" << d->getCumulativeAmount()
			  << ",\"rate\":" << d->getInstantaneousRate() << "}";
		}
		o << "]}";
		emit(o.str());
	}

	void processGetSetpointsResponse(cea2045GetSetpointsResponse1 *m) override
	{
		std::ostringstream o;
		o << "{\"t\":\"setpoint\",\"rc\":" << (int)m->responseCode
		  << ",\"device_type\":" << m->getDeviceType()
		  << ",\"units\":" << (int)m->units
		  << ",\"setpoint1\":" << m->getSetpoint1() << "}";
		emit(o.str());
	}

	void processGetSetpointsResponse(cea2045GetSetpointsResponse2 *m) override
	{
		std::ostringstream o;
		o << "{\"t\":\"setpoint\",\"rc\":" << (int)m->responseCode
		  << ",\"device_type\":" << m->getDeviceType()
		  << ",\"units\":" << (int)m->units
		  << ",\"setpoint1\":" << m->getSetpoint1()
		  << ",\"setpoint2\":" << m->getSetpoint2() << "}";
		emit(o.str());
	}

	void processGetPresentTemperatureResponse(cea2045GetPresentTemperatureResponse *m) override
	{
		std::ostringstream o;
		o << "{\"t\":\"present_temperature\",\"rc\":" << (int)m->responseCode
		  << ",\"device_type\":" << m->getDeviceType()
		  << ",\"units\":" << (int)m->units
		  << ",\"value\":" << m->getPresentTemperature() << "}";
		emit(o.str());
	}

	void processGetTemperatureOffsetResponse(cea2045GetTemperateOffsetResponse *m) override
	{
		std::ostringstream o;
		o << "{\"t\":\"temperature_offset\",\"rc\":" << (int)m->responseCode
		  << ",\"units\":" << (int)m->units
		  << ",\"offset\":" << (int)m->currentOffset << "}";
		emit(o.str());
	}

	void processSetSetpointsResponse(cea2045IntermediateResponse *m) override
	{
		emitIntermediate("set_setpoints", m);
	}

	void processSetTemperatureOffsetResponse(cea2045IntermediateResponse *m) override
	{
		emitIntermediate("set_temperature_offset", m);
	}

	void processSetEnergyPriceResponse(cea2045IntermediateResponse *m) override
	{
		emitIntermediate("set_energy_price", m);
	}

	void processStartCyclingResponse(cea2045IntermediateResponse *m) override
	{
		emitIntermediate("start_cycling", m);
	}

	void processTerminateCyclingResponse(cea2045IntermediateResponse *m) override
	{
		emitIntermediate("terminate_cycling", m);
	}

	void processGetUTCTimeResponse(cea2045GetUTCTimeResponse *) override
	{
		emit("{\"t\":\"utc_time\"}");
	}

	void processAckReceived(MessageCode messageCode) override
	{
		std::ostringstream o;
		o << "{\"t\":\"link_ack\",\"message\":" << (int)messageCode << "}";
		emit(o.str());
	}

	void processNakReceived(LinkLayerNakCode nak, MessageCode messageCode) override
	{
		std::ostringstream o;
		o << "{\"t\":\"link_nak\",\"message\":" << (int)messageCode
		  << ",\"reason\":" << quote(nakCodeName(nak)) << "}";
		emit(o.str());
	}

	void processAppAckReceived(cea2045Basic *) override
	{
		emit("{\"t\":\"app_ack\"}");
	}

	void processAppNakReceived(cea2045Basic *) override
	{
		emit("{\"t\":\"app_nak\"}");
	}

	void processOperationalStateReceived(cea2045Basic *m) override
	{
		std::ostringstream o;
		o << "{\"t\":\"operational_state\",\"state\":" << (int)m->opCode2 << "}";
		emit(o.str());
	}

	void processAppCustomerOverride(cea2045Basic *m) override
	{
		std::ostringstream o;
		o << "{\"t\":\"customer_override\",\"value\":" << (int)m->opCode2 << "}";
		emit(o.str());
	}

	void processIncompleteMessage(const unsigned char *buffer, unsigned int numBytes) override
	{
		std::ostringstream o;
		o << "{\"t\":\"incomplete\",\"bytes\":" << numBytes
		  << ",\"raw\":" << quote(hexDump(buffer, numBytes)) << "}";
		emit(o.str());
	}

private:
	void emitIntermediate(const char *name, cea2045IntermediateResponse *m)
	{
		std::ostringstream o;
		o << "{\"t\":" << quote(name) << ",\"rc\":" << (int)m->responseCode << "}";
		emit(o.str());
	}
};

void emitResult(const std::string &command, ResponseCodes codes)
{
	std::ostringstream o;
	o << "{\"t\":\"result\",\"cmd\":" << quote(command)
	  << ",\"status\":" << quote(responseCodeName(codes.responesCode))
	  << ",\"nak\":" << quote(nakCodeName(codes.nak)) << "}";
	emit(o.str());
}

void emitError(const std::string &message)
{
	emit("{\"t\":\"error\",\"message\":" + quote(message) + "}");
}

void configureLogging()
{
	const char *path = getenv("CTA2045_LOG");
	el::Configurations config;
	config.setToDefault();
	config.setGlobally(el::ConfigurationType::ToStandardOutput, "false");
	config.setGlobally(el::ConfigurationType::Filename, path ? path : "/tmp/cta2045-bridge.log");
	config.setGlobally(el::ConfigurationType::Format, "%datetime %level %msg");
	el::Loggers::reconfigureAllLoggers(config);
}

// The library resolves a request's future as soon as the link layer acks it,
// which is well before the tank sends the actual response. Firing the next
// request at that moment puts our bytes on the wire while the reply is still
// arriving, and the receive buffer surfaces the collision as a truncated
// frame. So every command is followed by a quiet period long enough for the
// response to land and be parsed.
void settle()
{
	static const int gapMS = []() {
		const char *env = getenv("CTA2045_COMMAND_GAP_MS");
		int value = env ? atoi(env) : 750;
		return value > 0 ? value : 750;
	}();
	std::this_thread::sleep_for(std::chrono::milliseconds(gapMS));
}

std::vector<std::string> tokenize(const std::string &line)
{
	std::vector<std::string> tokens;
	std::istringstream stream(line);
	std::string token;
	while (stream >> token)
		tokens.push_back(token);
	return tokens;
}

// Durations are the raw CTA-2045 byte, not minutes: the tank decodes it as
// seconds = value^2 * 2, and 0 and 255 both mean "until told otherwise". The
// supervisor owns that arithmetic; the bridge passes the byte through.
unsigned char toByte(const std::string &text)
{
	long value = strtol(text.c_str(), nullptr, 0);
	if (value < 0)   value = 0;
	if (value > 255) value = 255;
	return (unsigned char)value;
}

} // namespace

int main(int argc, char **argv)
{
	configureLogging();

	const char *portName = argc > 1 ? argv[1] : "/dev/serial0";
	CEA2045SerialPort port(portName);
	JsonUCM ucm;

	if (!port.open())
	{
		emitError(std::string("failed to open ") + portName + ": " + strerror(errno));
		return 1;
	}

	ICEA2045DeviceUCM *device = DeviceFactory::createUCM(&port, &ucm);
	device->start();

	device->querySuportDataLinkMessages().get();
	settle();
	device->queryMaxPayload().get();
	settle();
	device->querySuportIntermediateMessages().get();
	settle();
	device->intermediateGetDeviceInformation().get();
	settle();

	emit("{\"t\":\"ready\",\"port\":" + quote(portName) + "}");

	std::string line;
	while (std::getline(std::cin, line))
	{
		std::vector<std::string> argv = tokenize(line);
		if (argv.empty())
			continue;

		const std::string &cmd = argv[0];
		const std::string arg1 = argv.size() > 1 ? argv[1] : "";

		if (cmd == "quit")
			break;
		else if (cmd == "info")
			emitResult(cmd, device->intermediateGetDeviceInformation().get());
		else if (cmd == "state")
			emitResult(cmd, device->basicQueryOperationalState().get());
		else if (cmd == "commodity")
			emitResult(cmd, device->intermediateGetCommodity().get());
		else if (cmd == "setpoint")
			emitResult(cmd, device->intermediateGetSetPoint().get());
		else if (cmd == "temp")
			emitResult(cmd, device->intermediateGetPresentTemperature().get());
		else if (cmd == "offset")
			emitResult(cmd, device->intermediateGetTemperatureOffset().get());
		else if (cmd == "comm")
			emitResult(cmd, device->basicOutsideCommConnectionStatus(
			                    (OutsideCommuncatonStatusCode)toByte(arg1.empty() ? "1" : arg1)).get());
		else if (cmd == "shed")
			emitResult(cmd, device->basicShed(toByte(arg1)).get());
		else if (cmd == "endshed")
			emitResult(cmd, device->basicEndShed(toByte(arg1)).get());
		else if (cmd == "loadup")
			emitResult(cmd, device->basicLoadUp(toByte(arg1)).get());
		else if (cmd == "critical")
			emitResult(cmd, device->basicCriticalPeakEvent(toByte(arg1)).get());
		else if (cmd == "emergency")
			emitResult(cmd, device->basicGridEmergency(toByte(arg1)).get());
		else if (cmd == "powerlevel")
			emitResult(cmd, device->basicPowerLevel(toByte(arg1)).get());
		else if (cmd == "setsetpoint" && argv.size() >= 4)
			emitResult(cmd, device->intermediateSetSetpoint1(
			                    (unsigned short)strtol(argv[1].c_str(), nullptr, 0),
			                    argv[2] == "C" ? TemperatureUnits::C : TemperatureUnits::F,
			                    (unsigned short)strtol(argv[3].c_str(), nullptr, 0)).get());
		else
		{
			emitError("unknown command: " + cmd);
			continue;
		}

		settle();
	}

	device->shutDown();
	port.close();
	emit("{\"t\":\"stopped\"}");
	return 0;
}
