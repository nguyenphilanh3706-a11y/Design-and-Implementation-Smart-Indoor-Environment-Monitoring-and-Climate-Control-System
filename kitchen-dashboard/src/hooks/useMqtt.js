import { useState, useEffect, useRef } from "react";
import mqtt from "mqtt";

const DEVICE_ID = "esp32_kitchen_01";
const PREFIX = `iot/kitchen/${DEVICE_ID}`;

// Cổng WebSocket MQTT chuẩn theo bản thống nhất kỹ thuật
const BROKER_URL = "ws://localhost:9001"; 

export const useMqtt = () => {
  const clientRef = useRef(null);
  const [isConnected, setIsConnected] = useState(false);
  const [telemetry, setTelemetry] = useState({
    temperature: 0,
    humidity: 0,
    pollution_percent: 0,
    fan_state: 0,
    mode: "AUTO",
    timestamp: null,
  });
  const [deviceStatus, setDeviceStatus] = useState("OFFLINE");

  useEffect(() => {
    const client = mqtt.connect(BROKER_URL, {
      clientId: `web_${Math.random().toString(16).substring(2, 8)}`,
      clean: true,
      reconnectPeriod: 3000,
    });
    clientRef.current = client;

    client.on("connect", () => {
      setIsConnected(true);
      client.subscribe(`${PREFIX}/telemetry`, { qos: 0 });
      client.subscribe(`${PREFIX}/status`, { qos: 1 });
      client.subscribe(`${PREFIX}/actuator/state`, { qos: 1 });
    });

    client.on("message", (topic, message) => {
      try {
        const payload = JSON.parse(message.toString());
        if (topic.endsWith("/telemetry")) {
          setTelemetry((prev) => ({ ...prev, ...payload }));
        } else if (topic.endsWith("/status")) {
          setDeviceStatus(payload.status);
        } else if (topic.endsWith("/actuator/state")) {
          setTelemetry((prev) => ({ ...prev, fan_state: payload.state }));
        }
      } catch (err) {
        console.error("Lỗi parse JSON:", err);
      }
    });

    client.on("close", () => setIsConnected(false));

    return () => {
      if (client) client.end();
    };
  }, []);

  const setFanState = (state) => {
    if (clientRef.current && isConnected) {
      const payload = JSON.stringify({ command: "SET_FAN", state: state ? 1 : 0 });
      clientRef.current.publish(`${PREFIX}/actuator/set`, payload, { qos: 1 });
    }
  };

  const setOperatingMode = (mode) => {
    if (clientRef.current && isConnected) {
      const payload = JSON.stringify({ mode });
      clientRef.current.publish(`${PREFIX}/mode/set`, payload, { qos: 1, retain: true });
    }
  };

  return { isConnected, telemetry, deviceStatus, setFanState, setOperatingMode };
};