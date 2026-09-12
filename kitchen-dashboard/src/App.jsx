import React, { useState, useEffect } from "react";
import { useMqtt } from "./hooks/useMqtt";
import { Thermometer, Droplets, Wind, Fan, Activity } from "lucide-react";
import { Line } from "react-chartjs-2";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
} from "chart.js";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend);

export default function App() {
  const { isConnected, telemetry, deviceStatus, setFanState, setOperatingMode } = useMqtt();
  const [freshness, setFreshness] = useState(0);
  const [chartData, setChartData] = useState({
    labels: [],
    datasets: [
      { label: "Nhiệt độ (°C)", data: [], borderColor: "#f97316", tension: 0.3 },
      { label: "Ô nhiễm (%)", data: [], borderColor: "#ef4444", tension: 0.3 },
    ],
  });

  // Tính Data Freshness mỗi giây
  useEffect(() => {
    const timer = setInterval(() => {
      if (telemetry.timestamp) {
        const diff = Date.now() - new Date(telemetry.timestamp).getTime();
        setFreshness(Math.max(0, diff));
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [telemetry.timestamp]);

  // Cập nhật dữ liệu đồ thị thời gian thực
  useEffect(() => {
    if (!telemetry.timestamp) return;
    const timeLabel = new Date(telemetry.timestamp).toLocaleTimeString();

    setChartData((prev) => {
      const newLabels = [...prev.labels, timeLabel].slice(-15);
      const newTemp = [...prev.datasets[0].data, telemetry.temperature].slice(-15);
      const newPollution = [...prev.datasets[1].data, telemetry.pollution_percent].slice(-15);

      return {
        labels: newLabels,
        datasets: [
          { ...prev.datasets[0], data: newTemp },
          { ...prev.datasets[1], data: newPollution },
        ],
      };
    });
  }, [telemetry.timestamp, telemetry.temperature, telemetry.pollution_percent]);

  // Dải màu cảnh báo ô nhiễm
  const getPollutionColor = (val) => {
    if (val < 25) return "text-emerald-400 border-emerald-500/30 bg-emerald-500/10";
    if (val < 40) return "text-amber-400 border-amber-500/30 bg-amber-500/10";
    return "text-rose-500 border-rose-500/30 bg-rose-500/10 animate-pulse";
  };

  // Trạng thái Data Freshness
  const getFreshnessStatus = () => {
    if (freshness < 3000) return { color: "bg-emerald-500", text: "Dữ liệu tươi (< 3s)" };
    if (freshness <= 10000) return { color: "bg-amber-500", text: `Trễ mạng (${(freshness / 1000).toFixed(1)}s)` };
    return { color: "bg-rose-500", text: "Mất kết nối / Stale Data" };
  };

  const status = getFreshnessStatus();

  return (
    <div className="p-4 md:p-8 max-w-7xl mx-auto space-y-6">
      {/* Header & Status Bar */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-slate-900 border border-slate-800 p-5 rounded-2xl">
        <div>
          <h1 className="text-xl md:text-2xl font-bold">Kitchen Air Quality Dashboard</h1>
          <p className="text-sm text-slate-400">Thiết bị: esp32_kitchen_01</p>
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <div className="flex items-center gap-2 bg-slate-800 px-3 py-1.5 rounded-full">
            <span className={`w-2.5 h-2.5 rounded-full ${isConnected ? "bg-emerald-400" : "bg-rose-500"}`} />
            <span>Broker: {isConnected ? "Connected" : "Disconnected"}</span>
          </div>
          <div className="flex items-center gap-2 bg-slate-800 px-3 py-1.5 rounded-full">
            <span className={`w-2.5 h-2.5 rounded-full ${status.color}`} />
            <span>{status.text}</span>
          </div>
        </div>
      </header>

      {/* Grid thẻ thông số */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 p-5 rounded-2xl">
          <div className="flex justify-between items-center text-slate-400 mb-2">
            <span>Nhiệt độ</span>
            <Thermometer className="w-5 h-5 text-orange-400" />
          </div>
          <div className="text-3xl font-semibold">{telemetry.temperature.toFixed(1)} °C</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 p-5 rounded-2xl">
          <div className="flex justify-between items-center text-slate-400 mb-2">
            <span>Độ ẩm</span>
            <Droplets className="w-5 h-5 text-cyan-400" />
          </div>
          <div className="text-3xl font-semibold">{telemetry.humidity.toFixed(1)} %</div>
        </div>

        <div className={`border p-5 rounded-2xl ${getPollutionColor(telemetry.pollution_percent)}`}>
          <div className="flex justify-between items-center mb-2">
            <span className="text-slate-300">Ô nhiễm (MQ-135)</span>
            <Wind className="w-5 h-5" />
          </div>
          <div className="text-3xl font-semibold">{telemetry.pollution_percent.toFixed(1)} %</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 p-5 rounded-2xl">
          <div className="flex justify-between items-center text-slate-400 mb-2">
            <span>Trạng thái Quạt</span>
            <Fan className={`w-5 h-5 ${telemetry.fan_state ? "text-emerald-400 animate-spin" : "text-slate-500"}`} />
          </div>
          <div className="text-3xl font-semibold">
            {telemetry.fan_state ? <span className="text-emerald-400">ON</span> : <span className="text-slate-500">OFF</span>}
          </div>
        </div>
      </div>

      {/* Cụm điều khiển Override */}
      <div className="bg-slate-900 border border-slate-800 p-6 rounded-2xl flex flex-col md:flex-row items-center justify-between gap-6">
        <div className="flex items-center gap-4 w-full md:w-auto">
          <span className="text-sm font-medium text-slate-300">Chế độ vận hành:</span>
          <div className="flex bg-slate-800 p-1 rounded-xl">
            <button
              onClick={() => setOperatingMode("AUTO")}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition ${
                telemetry.mode === "AUTO" ? "bg-cyan-600 text-white" : "text-slate-400 hover:text-white"
              }`}
            >
              AUTO
            </button>
            <button
              onClick={() => setOperatingMode("MANUAL")}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition ${
                telemetry.mode === "MANUAL" ? "bg-cyan-600 text-white" : "text-slate-400 hover:text-white"
              }`}
            >
              MANUAL
            </button>
          </div>
        </div>

        <div className="flex items-center gap-4 w-full md:w-auto justify-end">
          <span className="text-sm text-slate-400">Điều khiển quạt:</span>
          <button
            disabled={telemetry.mode === "AUTO"}
            onClick={() => setFanState(!telemetry.fan_state)}
            className={`px-6 py-2 rounded-xl font-medium transition shadow-lg ${
              telemetry.mode === "AUTO"
                ? "bg-slate-800 text-slate-600 cursor-not-allowed border border-slate-700"
                : telemetry.fan_state
                ? "bg-rose-600 hover:bg-rose-500 text-white shadow-rose-600/30"
                : "bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/30"
            }`}
          >
            {telemetry.fan_state ? "TẮT QUẠT" : "BẬT QUẠT"}
          </button>
        </div>
      </div>

      {/* Biểu đồ Realtime */}
      <div className="bg-slate-900 border border-slate-800 p-6 rounded-2xl">
        <div className="flex items-center gap-2 mb-4 text-slate-300">
          <Activity className="w-5 h-5 text-cyan-400" />
          <h2 className="font-semibold">Lịch sử Đo Thời Gian Thực</h2>
        </div>
        <div className="h-72 w-full">
          <Line
            data={chartData}
            options={{
              responsive: true,
              maintainAspectRatio: false,
              scales: {
                y: { grid: { color: "#1e293b" }, ticks: { color: "#94a3b8" } },
                x: { grid: { color: "#1e293b" }, ticks: { color: "#94a3b8" } },
              },
              plugins: { legend: { labels: { color: "#cbd5e1" } } },
            }}
          />
        </div>
      </div>
    </div>
  );
}