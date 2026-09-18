import React, { useState, useEffect, useRef } from 'react';
import mqtt from 'mqtt';
import Chart from 'chart.js/auto';

const WS_URL = import.meta.env.VITE_WS_URL || 'ws://10.245.159.89:9001';
const API_URL = import.meta.env.VITE_API_URL || 'http://10.245.159.89:8000';
const WS_USER = import.meta.env.VITE_WS_USER || 'web_dashboard';
const WS_PASS = import.meta.env.VITE_WS_PASS || 'WebAppPass';

export default function App() {
  const [telemetry, setTelemetry] = useState({
    device_id: '--',
    seq: 0,
    timestamp: null,
    temperature: 0,
    humidity: 0,
    pollution_percent: 0,
    rs_ro_ratio: 0,
    fan_state: 0,
    mode: 'AUTO',
    network_status: 'DISCONNECTED',
  });

  const [brokerConnected, setBrokerConnected] = useState(false);
  const [freshnessMs, setFreshnessMs] = useState(null);
  const [apiLoading, setApiLoading] = useState(false);
  const [apiError, setApiError] = useState('');

  const chartRef = useRef(null);
  const chartInstance = useRef(null);
  const historyData = useRef({ labels: [], temp: [], pollution: [] });

  // 1. Khởi tạo Biểu đồ thời gian thực (Chart.js)
  useEffect(() => {
    if (!chartRef.current) return;
    const ctx = chartRef.current.getContext('2d');

    chartInstance.current = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          {
            label: 'Nhiệt độ (°C)',
            borderColor: '#f97316',
            backgroundColor: 'rgba(249, 115, 22, 0.1)',
            data: [],
            yAxisID: 'yTemp',
            tension: 0.3,
            fill: true,
          },
          {
            label: 'Ô nhiễm (%)',
            borderColor: '#ef4444',
            backgroundColor: 'rgba(239, 68, 68, 0.1)',
            data: [],
            yAxisID: 'yPol',
            tension: 0.3,
            fill: true,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            grid: { color: '#334155' },
            ticks: { color: '#94a3b8' },
          },
          yTemp: {
            type: 'linear',
            position: 'left',
            grid: { color: '#334155' },
            ticks: { color: '#f97316' },
            title: { display: true, text: '°C', color: '#f97316' },
          },
          yPol: {
            type: 'linear',
            position: 'right',
            grid: { drawOnChartArea: false },
            ticks: { color: '#ef4444' },
            title: { display: true, text: '% Ô nhiễm', color: '#ef4444' },
            min: 0,
            max: 100,
          },
        },
        plugins: {
          legend: { labels: { color: '#f8fafc' } },
        },
      },
    });

    return () => {
      if (chartInstance.current) chartInstance.current.destroy();
    };
  }, []);

  // 2. Kết nối MQTT over WebSockets
  useEffect(() => {
    const clientId = `web_${Math.random().toString(16).substring(2, 8)}`;
    const client = mqtt.connect(WS_URL, {
      username: WS_USER,
      password: WS_PASS,
      clientId,
      clean: true,
      reconnectPeriod: 3000,
    });

    client.on('connect', () => {
      setBrokerConnected(true);
      client.subscribe('iot/kitchen/esp32_kitchen_01/#');
    });

    client.on('message', (_, message) => {
      try {
        const payload = JSON.parse(message.toString());
        setTelemetry(payload);

        // Cập nhật biểu đồ lịch sử (giữ tối đa 20 điểm)
        const timeLabel = new Date(payload.timestamp).toLocaleTimeString();
        const h = historyData.current;
        h.labels.push(timeLabel);
        h.temp.push(payload.temperature);
        h.pollution.push(payload.pollution_percent);

        if (h.labels.length > 20) {
          h.labels.shift();
          h.temp.shift();
          h.pollution.shift();
        }

        if (chartInstance.current) {
          chartInstance.current.data.labels = [...h.labels];
          chartInstance.current.data.datasets[0].data = [...h.temp];
          chartInstance.current.data.datasets[1].data = [...h.pollution];
          chartInstance.current.update('none');
        }
      } catch (err) {
        console.error('Lỗi parse JSON MQTT:', err);
      }
    });

    client.on('error', () => setBrokerConnected(false));
    client.on('close', () => setBrokerConnected(false));

    return () => client.end(true);
  }, []);

  // 3. Tính độ tươi dữ liệu mỗi 500ms
  useEffect(() => {
    const timer = setInterval(() => {
      if (telemetry.timestamp) {
        const delay = Date.now() - new Date(telemetry.timestamp).getTime();
        setFreshnessMs(Math.max(0, delay));
      }
    }, 500);
    return () => clearInterval(timer);
  }, [telemetry.timestamp]);

  // 4. Các hàm gọi REST API điều khiển
  const handleToggleMode = async () => {
    const nextMode = telemetry.mode === 'AUTO' ? 'MANUAL' : 'AUTO';
    setApiLoading(true);
    setApiError('');
    try {
      const res = await fetch(`${API_URL}/api/v1/kitchen/mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: nextMode }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.reason || 'Lỗi đổi chế độ');
      setTelemetry((prev) => ({ ...prev, mode: nextMode }));
    } catch (err) {
      setApiError(err.message);
    } finally {
      setApiLoading(false);
    }
  };

  const handleToggleFan = async () => {
    if (telemetry.mode === 'AUTO') return;
    const nextState = telemetry.fan_state === 1 ? 0 : 1;
    setApiLoading(true);
    setApiError('');
    try {
      const res = await fetch(`${API_URL}/api/v1/kitchen/actuator`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ state: nextState }),
      });
      const data = await res.json();
      if (!res.ok) {
        if (data.reason === 'IGNORED_AUTO_MODE') {
          throw new Error('Thiết bị đang ở chế độ AUTO, không thể can thiệp!');
        }
        throw new Error(data.reason || 'Lỗi điều khiển quạt');
      }
      setTelemetry((prev) => ({ ...prev, fan_state: nextState }));
    } catch (err) {
      setApiError(err.message);
    } finally {
      setApiLoading(false);
    }
  };

  // Helper tính màu trạng thái độ tươi
  const getFreshnessStatus = () => {
    if (freshnessMs === null) return { text: 'Chưa có tin', color: 'bg-gray-500' };
    if (freshnessMs < 3000) return { text: `Tươi (${(freshnessMs / 1000).toFixed(1)}s)`, color: 'bg-emerald-500 animate-pulse' };
    if (freshnessMs <= 10000) return { text: `Trễ (${(freshnessMs / 1000).toFixed(1)}s)`, color: 'bg-amber-500' };
    return { text: `Stale Data (>${(freshnessMs / 1000).toFixed(0)}s)`, color: 'bg-rose-500' };
  };

  // Helper dải màu cho % Ô nhiễm
  const getPollutionColor = (val) => {
    if (val <= 30) return { badge: 'Tốt', border: 'border-emerald-500', text: 'text-emerald-400', bg: 'bg-emerald-500/10' };
    if (val <= 60) return { badge: 'Trung bình', border: 'border-amber-500', text: 'text-amber-400', bg: 'bg-amber-500/10' };
    return { badge: 'Nguy hại', border: 'border-rose-500', text: 'text-rose-400', bg: 'bg-rose-500/10' };
  };

  const freshness = getFreshnessStatus();
  const polStyle = getPollutionColor(telemetry.pollution_percent);

  return (
    <div className="min-h-screen p-4 md:p-8 max-w-7xl mx-auto space-y-6">
      {/* Header bar */}
      <header className="flex flex-col md:flex-row md:items-center justify-between bg-slate-800/80 backdrop-blur p-5 rounded-2xl border border-slate-700 shadow-lg gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            Kitchen IoT Dashboard
            <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-300 font-mono">
              {telemetry.device_id}
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">Hệ thống giám sát không khí và điều khiển thông gió</p>
        </div>

        {/* Trạng thái Broker & Độ tươi dữ liệu */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 bg-slate-900/60 px-3 py-1.5 rounded-lg border border-slate-700 text-xs">
            <span className={`w-2.5 h-2.5 rounded-full ${brokerConnected ? 'bg-emerald-500' : 'bg-rose-500'}`}></span>
            <span>Broker: {brokerConnected ? 'Đã nối' : 'Mất kết nối'}</span>
          </div>

          <div className="flex items-center gap-2 bg-slate-900/60 px-3 py-1.5 rounded-lg border border-slate-700 text-xs">
            <span className={`w-2.5 h-2.5 rounded-full ${freshness.color}`}></span>
            <span>Độ tươi: {freshness.text}</span>
          </div>
        </div>
      </header>

      {/* Thông báo lỗi REST nếu có */}
      {apiError && (
        <div className="bg-rose-500/20 border border-rose-500 text-rose-300 px-4 py-3 rounded-xl text-sm flex justify-between items-center">
          <span>{apiError}</span>
          <button onClick={() => setApiError('')} className="font-bold ml-2">✕</button>
        </div>
      )}

      {/* Grid thẻ đo cảm biến */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Nhiệt độ */}
        <div className="bg-slate-800 p-5 rounded-2xl border border-slate-700 shadow flex flex-col justify-between">
          <span className="text-xs uppercase font-semibold text-slate-400 tracking-wider">Nhiệt độ</span>
          <div className="my-3 flex items-baseline gap-2">
            <span className="text-4xl font-extrabold text-orange-400">{telemetry.temperature}</span>
            <span className="text-lg text-slate-400">°C</span>
          </div>
          <span className="text-xs text-slate-500">Ngưỡng bếp thông thường 25 - 35°C</span>
        </div>

        {/* Độ ẩm */}
        <div className="bg-slate-800 p-5 rounded-2xl border border-slate-700 shadow flex flex-col justify-between">
          <span className="text-xs uppercase font-semibold text-slate-400 tracking-wider">Độ ẩm</span>
          <div className="my-3 flex items-baseline gap-2">
            <span className="text-4xl font-extrabold text-sky-400">{telemetry.humidity}</span>
            <span className="text-lg text-slate-400">%</span>
          </div>
          <span className="text-xs text-slate-500">Độ ẩm không khí tức thời</span>
        </div>

        {/* % Ô nhiễm */}
        <div className={`p-5 rounded-2xl border ${polStyle.border} ${polStyle.bg} shadow flex flex-col justify-between`}>
          <div className="flex justify-between items-center">
            <span className="text-xs uppercase font-semibold text-slate-400 tracking-wider">Nồng độ ô nhiễm</span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-bold ${polStyle.text} bg-slate-900/60 border border-current`}>
              {polStyle.badge}
            </span>
          </div>
          <div className="my-3 flex items-baseline gap-2">
            <span className={`text-4xl font-extrabold ${polStyle.text}`}>{telemetry.pollution_percent}</span>
            <span className="text-lg text-slate-400">%</span>
          </div>
          <span className="text-xs text-slate-500">Tỷ lệ Rs/Ro: {telemetry.rs_ro_ratio}</span>
        </div>

        {/* Cụm điều khiển Override */}
        <div className="bg-slate-800 p-5 rounded-2xl border border-slate-700 shadow flex flex-col justify-between">
          <div className="flex justify-between items-center">
            <span className="text-xs uppercase font-semibold text-slate-400 tracking-wider">Điều khiển Quạt</span>
            <span className={`text-xs px-2 py-0.5 rounded font-mono font-bold ${telemetry.fan_state === 1 ? 'bg-emerald-500 text-white' : 'bg-slate-700 text-slate-400'}`}>
              QUẠT: {telemetry.fan_state === 1 ? 'BẬT' : 'TẮT'}
            </span>
          </div>

          <div className="space-y-3 my-2">
            {/* Chuyển chế độ AUTO / MANUAL */}
            <div className="flex items-center justify-between bg-slate-900/70 p-2 rounded-xl">
              <span className="text-xs text-slate-300 font-medium">Chế độ:</span>
              <button
                disabled={apiLoading}
                onClick={handleToggleMode}
                className={`text-xs font-bold px-3 py-1.5 rounded-lg transition ${
                  telemetry.mode === 'MANUAL'
                    ? 'bg-amber-500 text-slate-950 hover:bg-amber-400'
                    : 'bg-indigo-600 text-white hover:bg-indigo-500'
                }`}
              >
                {telemetry.mode}
              </button>
            </div>

            {/* Nút override BẬT / TẮT Quạt */}
            <button
              disabled={telemetry.mode === 'AUTO' || apiLoading}
              onClick={handleToggleFan}
              className={`w-full py-2 px-4 rounded-xl text-sm font-bold transition flex items-center justify-center gap-2 ${
                telemetry.mode === 'AUTO'
                  ? 'bg-slate-700/50 text-slate-500 cursor-not-allowed border border-dashed border-slate-600'
                  : telemetry.fan_state === 1
                  ? 'bg-rose-600 text-white hover:bg-rose-500'
                  : 'bg-emerald-600 text-white hover:bg-emerald-500'
              }`}
            >
              {telemetry.mode === 'AUTO' ? (
                'Khóa (Chỉ mở ở MANUAL)'
              ) : telemetry.fan_state === 1 ? (
                'TẮT QUẠT'
              ) : (
                'BẬT QUẠT'
              )}
            </button>
          </div>

          <span className="text-[11px] text-slate-500">Chuyển sang MANUAL để override thiết bị</span>
        </div>
      </div>

      {/* Biểu đồ thời gian thực */}
      <div className="bg-slate-800 p-5 rounded-2xl border border-slate-700 shadow">
        <h2 className="text-base font-semibold text-slate-200 mb-4 flex items-center justify-between">
          <span>Lịch sử Nhiệt độ & Ô nhiễm thời gian thực</span>
          <span className="text-xs font-normal text-slate-400">20 mẫu gần nhất</span>
        </h2>
        <div className="h-72 md:h-80 w-full">
          <canvas ref={chartRef}></canvas>
        </div>
      </div>
    </div>
  );
}