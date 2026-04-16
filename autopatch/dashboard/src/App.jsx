import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Area, AreaChart,
} from 'recharts'

// ─── Config ────────────────────────────────────────────────────────────────
const API_URL = 'http://localhost:8000'

// ─── Tag → colour map for log lines ────────────────────────────────────────
const TAG_STYLES = {
  '[END]':      'text-green-400',
  '[SUMMARY]':  'text-green-300',
  '[CRITIC]':   'text-yellow-400',
  '[EVOLVER]':  'text-purple-400',
  '[MEMORY]':   'text-blue-400',
  '[PLAN]':     'text-cyan-400',
  '[CODE]':     'text-sky-400',
  '[STEP]':     'text-slate-300',
  '[RESET]':    'text-slate-400',
  '[READ]':     'text-slate-400',
  '[AUTOPATCH]':'text-green-500',
  'ERROR':      'text-red-400',
  'WARNING':    'text-orange-400',
}

function tagStyle(line) {
  for (const [tag, cls] of Object.entries(TAG_STYLES)) {
    if (line.includes(tag)) return cls
  }
  return 'text-slate-400'
}

// ─── Animated counter ───────────────────────────────────────────────────────
function AnimatedNumber({ value, decimals = 0, suffix = '' }) {
  const [display, setDisplay] = useState(value)
  const prevRef = useRef(value)

  useEffect(() => {
    const from = prevRef.current
    const to   = value
    const dur  = 600
    const start = performance.now()

    const tick = (now) => {
      const t = Math.min((now - start) / dur, 1)
      const eased = 1 - Math.pow(1 - t, 3)
      setDisplay(from + (to - from) * eased)
      if (t < 1) requestAnimationFrame(tick)
      else { setDisplay(to); prevRef.current = to }
    }
    requestAnimationFrame(tick)
  }, [value])

  return (
    <span>
      {decimals > 0 ? display.toFixed(decimals) : Math.round(display)}
      {suffix}
    </span>
  )
}

// ─── Grid background ────────────────────────────────────────────────────────
function GridBackground() {
  return (
    <div className="fixed inset-0 pointer-events-none overflow-hidden">
      {/* dot grid */}
      <div
        className="absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage:
            'radial-gradient(circle, #4ade80 1px, transparent 1px)',
          backgroundSize: '32px 32px',
        }}
      />
      {/* corner glow */}
      <div className="absolute -top-40 -left-40 w-96 h-96 bg-green-500 rounded-full opacity-[0.04] blur-3xl" />
      <div className="absolute -bottom-40 -right-40 w-96 h-96 bg-emerald-600 rounded-full opacity-[0.04] blur-3xl" />
      {/* scanline */}
      <div className="scanline-effect" />
    </div>
  )
}

// ─── Status badge ────────────────────────────────────────────────────────────
function StatusBadge({ status }) {
  const configs = {
    idle:    { label: 'IDLE',    dot: 'bg-slate-500', text: 'text-slate-400', ring: 'ring-slate-700' },
    running: { label: 'RUNNING', dot: 'bg-green-400',  text: 'text-green-400', ring: 'ring-green-900' },
    done:    { label: 'DONE',    dot: 'bg-blue-400',   text: 'text-blue-400',  ring: 'ring-blue-900' },
  }
  const c = configs[status] || configs.idle

  return (
    <motion.div
      key={status}
      initial={{ opacity: 0, scale: 0.8 }}
      animate={{ opacity: 1, scale: 1 }}
      className={`inline-flex items-center gap-2 px-3 py-1 rounded-full ring-1 ${c.ring} bg-gray-950/80`}
    >
      <span
        className={`w-2 h-2 rounded-full ${c.dot} ${status === 'running' ? 'animate-pulse' : ''}`}
      />
      <span className={`text-xs font-mono font-semibold tracking-widest ${c.text}`}>{c.label}</span>
    </motion.div>
  )
}

// ─── Stat card ───────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, accent, icon, delay = 0 }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      whileHover={{ y: -4, transition: { duration: 0.2 } }}
      className="stat-card relative flex-1 min-w-0 bg-gray-900/60 border border-gray-800 rounded-2xl p-5 overflow-hidden backdrop-blur-sm"
    >
      {/* glow on hover */}
      <div className={`absolute inset-0 opacity-0 hover:opacity-100 transition-opacity duration-500 rounded-2xl ${accent} blur-xl`} />
      <div className="relative">
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs font-mono text-slate-500 tracking-widest uppercase">{label}</span>
          <span className="text-lg">{icon}</span>
        </div>
        <div className={`text-3xl font-black font-mono ${accent.replace('bg-', 'text-').replace('/10', '-400')}`}>
          {value}
        </div>
        {sub && <div className="text-xs text-slate-600 font-mono mt-1">{sub}</div>}
      </div>
    </motion.div>
  )
}

// ─── Custom chart tooltip ─────────────────────────────────────────────────────
function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-gray-900/95 border border-green-900/60 rounded-xl px-4 py-2 shadow-2xl backdrop-blur-sm">
      <div className="text-xs text-slate-500 font-mono mb-1">Episode {label}</div>
      <div className="text-green-400 font-mono font-bold text-base">
        {payload[0]?.value?.toFixed(3)}
      </div>
    </div>
  )
}

// ─── Reward chart ────────────────────────────────────────────────────────────
function RewardChart({ scores }) {
  const data = scores.map((s, i) => ({ ep: i + 1, score: parseFloat(s.toFixed(4)) }))
  const avg  = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-mono font-semibold text-slate-300 tracking-widest uppercase">
          ⚡ Reward Curve
        </h2>
        {avg !== null && (
          <span className="text-xs font-mono text-green-400/70">
            avg {avg.toFixed(3)}
          </span>
        )}
      </div>

      {data.length === 0 ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <div className="text-4xl mb-3 animate-pulse">📈</div>
            <p className="text-slate-600 font-mono text-sm">Waiting for episodes…</p>
          </div>
        </div>
      ) : (
        <div className="flex-1">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="scoreGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#4ade80" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#4ade80" stopOpacity={0.02} />
                </linearGradient>
                <filter id="glow">
                  <feGaussianBlur stdDeviation="3" result="coloredBlur" />
                  <feMerge>
                    <feMergeNode in="coloredBlur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis
                dataKey="ep"
                tick={{ fill: '#4b5563', fontSize: 11, fontFamily: 'JetBrains Mono' }}
                tickLine={false}
                axisLine={{ stroke: '#1f2937' }}
                label={{ value: 'Episode', position: 'insideBottom', fill: '#4b5563', fontSize: 10, fontFamily: 'JetBrains Mono', dy: 8 }}
              />
              <YAxis
                domain={[0, 1]}
                tick={{ fill: '#4b5563', fontSize: 11, fontFamily: 'JetBrains Mono' }}
                tickLine={false}
                axisLine={{ stroke: '#1f2937' }}
                tickFormatter={v => v.toFixed(1)}
              />
              <Tooltip content={<ChartTooltip />} />
              {avg !== null && (
                <ReferenceLine
                  y={avg}
                  stroke="#4ade80"
                  strokeDasharray="4 4"
                  strokeOpacity={0.35}
                />
              )}
              <Area
                type="monotone"
                dataKey="score"
                stroke="#4ade80"
                strokeWidth={2.5}
                fill="url(#scoreGrad)"
                dot={{ fill: '#4ade80', r: 4, strokeWidth: 0, filter: 'url(#glow)' }}
                activeDot={{ r: 6, fill: '#86efac', filter: 'url(#glow)' }}
                isAnimationActive={true}
                animationDuration={600}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}

// ─── Agent log ───────────────────────────────────────────────────────────────
function AgentLog({ logs }) {
  const bottomRef = useRef(null)
  const containerRef = useRef(null)
  const [autoScroll, setAutoScroll] = useState(true)

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, autoScroll])

  const handleScroll = () => {
    const el = containerRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    setAutoScroll(atBottom)
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-mono font-semibold text-slate-300 tracking-widest uppercase">
          🤖 Agent Log
        </h2>
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-slate-600">{logs.length} lines</span>
          {!autoScroll && (
            <button
              onClick={() => { setAutoScroll(true); bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }}
              className="text-xs font-mono text-green-500 hover:text-green-400 transition-colors"
            >
              ↓ scroll to bottom
            </button>
          )}
        </div>
      </div>

      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto font-mono text-xs leading-relaxed scrollbar-thin space-y-[2px] pr-1"
      >
        <AnimatePresence initial={false}>
          {logs.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <div className="text-3xl mb-2 opacity-40">💻</div>
                <p className="text-slate-700 text-xs">Agent logs will appear here…</p>
              </div>
            </div>
          ) : (
            logs.map((line, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.18, ease: 'easeOut' }}
                className={`${tagStyle(line)} px-2 py-[1px] rounded hover:bg-white/[0.03] transition-colors`}
              >
                <span className="text-slate-700 select-none mr-2">
                  {String(i + 1).padStart(4, '0')}
                </span>
                {line}
              </motion.div>
            ))
          )}
        </AnimatePresence>
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

// ─── Run button ──────────────────────────────────────────────────────────────
function RunButton({ running, onClick, episode, totalEpisodes }) {
  const labels = running
    ? [`Running ${episode}/${totalEpisodes}`, 'Processing…', 'Fixing bugs…', 'Patching…']
    : ['▶  Run AutoPatch', '▶  Start Run', '▶  Launch Agents']

  const [labelIdx, setLabelIdx] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setLabelIdx(i => (i + 1) % labels.length), 2200)
    return () => clearInterval(id)
  }, [running, labels.length])

  return (
    <motion.button
      whileTap={{ scale: 0.97 }}
      whileHover={!running ? { scale: 1.03 } : {}}
      onClick={onClick}
      disabled={running}
      className={`
        relative overflow-hidden px-8 py-3 rounded-xl font-mono font-bold text-sm
        tracking-widest transition-all duration-300 min-w-[200px]
        ${running
          ? 'bg-gray-800 text-slate-500 cursor-not-allowed ring-1 ring-gray-700'
          : 'bg-green-500 text-gray-950 hover:bg-green-400 shadow-lg shadow-green-500/25 hover:shadow-green-400/40'
        }
      `}
    >
      {/* shimmer on idle */}
      {!running && (
        <span className="absolute inset-0 shimmer-effect rounded-xl" />
      )}
      {/* progress bar */}
      {running && totalEpisodes > 0 && (
        <motion.span
          className="absolute inset-y-0 left-0 bg-green-900/40 rounded-xl"
          initial={{ width: '0%' }}
          animate={{ width: `${(episode / totalEpisodes) * 100}%` }}
          transition={{ duration: 0.6, ease: 'easeOut' }}
        />
      )}
      <AnimatePresence mode="wait">
        <motion.span
          key={labelIdx}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.25 }}
          className="relative"
        >
          {labels[labelIdx]}
        </motion.span>
      </AnimatePresence>
    </motion.button>
  )
}

// ─── Episode slider ───────────────────────────────────────────────────────────
function EpisodeSlider({ value, onChange, disabled }) {
  const marks = [1, 3, 5, 10, 15, 20]
  return (
    <div className="flex items-center gap-4">
      <span className="text-xs font-mono text-slate-500 w-20 shrink-0">Episodes</span>
      <div className="flex gap-2 flex-wrap">
        {marks.map(m => (
          <button
            key={m}
            disabled={disabled}
            onClick={() => onChange(m)}
            className={`
              px-3 py-1 rounded-lg text-xs font-mono font-semibold transition-all duration-200
              ${value === m
                ? 'bg-green-500/20 text-green-400 ring-1 ring-green-500/50'
                : 'bg-gray-800 text-slate-500 hover:text-slate-300 hover:bg-gray-700'
              }
              disabled:opacity-40 disabled:cursor-not-allowed
            `}
          >
            {m}
          </button>
        ))}
      </div>
      <span className="text-green-400 font-mono font-bold text-sm ml-2">{value}×</span>
    </div>
  )
}

// ─── Main App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [episodes,  setEpisodes]  = useState(5)
  const [runId,     setRunId]     = useState(null)
  const [status,    setStatus]    = useState({ status: 'idle', episode: 0, scores: [] })
  const [logs,      setLogs]      = useState([])
  const [running,   setRunning]   = useState(false)

  const sseRef      = useRef(null)
  const pollRef     = useRef(null)

  // ── Stop polling + SSE ────────────────────────────────────────────────────
  const stopAll = useCallback(() => {
    clearInterval(pollRef.current)
    if (sseRef.current) { sseRef.current.close(); sseRef.current = null }
  }, [])

  // ── Status polling ────────────────────────────────────────────────────────
  const startPolling = useCallback((rid) => {
    clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API_URL}/status/${rid}`)
        const data = await r.json()
        setStatus(data)
        if (data.status === 'done') {
          clearInterval(pollRef.current)
          setRunning(false)
        }
      } catch (_) {}
    }, 1000)
  }, [])

  // ── SSE log stream ────────────────────────────────────────────────────────
  const startSSE = useCallback((rid) => {
    if (sseRef.current) sseRef.current.close()
    const es = new EventSource(`${API_URL}/logs/${rid}`)
    sseRef.current = es
    es.onmessage = (e) => {
      if (e.data === '[STREAM_END]') {
        es.close(); sseRef.current = null; return
      }
      setLogs(prev => [...prev, e.data])
    }
    es.onerror = () => { es.close(); sseRef.current = null }
  }, [])

  // ── Start run ─────────────────────────────────────────────────────────────
  const handleRun = useCallback(async () => {
    if (running) return
    try {
      setLogs([])
      setStatus({ status: 'running', episode: 0, scores: [] })
      setRunning(true)

      const r    = await fetch(`${API_URL}/run?episodes=${episodes}`, { method: 'POST' })
      const data = await r.json()
      const rid  = data.run_id

      setRunId(rid)
      startPolling(rid)
      startSSE(rid)
    } catch (err) {
      setLogs(prev => [...prev, `ERROR: Failed to connect to API — ${err.message}`])
      setRunning(false)
      setStatus(s => ({ ...s, status: 'idle' }))
    }
  }, [running, episodes, startPolling, startSSE])

  // ── Cleanup on unmount ────────────────────────────────────────────────────
  useEffect(() => () => stopAll(), [stopAll])

  // ── Derived stats ─────────────────────────────────────────────────────────
  const scores     = status.scores || []
  const avg        = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : 0
  const perfect    = scores.filter(s => s >= 0.99).length
  const uiStatus   = running ? 'running' : status.status === 'done' ? 'done' : 'idle'

  return (
    <div className="min-h-screen bg-gray-950 text-white overflow-x-hidden">
      <GridBackground />

      <div className="relative z-10 max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8 py-6 flex flex-col gap-6">

        {/* ── Header ───────────────────────────────────────────────────────── */}
        <motion.header
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
          className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4"
        >
          <div className="flex items-center gap-4">
            <motion.div
              animate={{ rotate: [0, 360] }}
              transition={{ duration: 20, repeat: Infinity, ease: 'linear' }}
              className="w-10 h-10 rounded-full bg-green-500/10 ring-1 ring-green-500/30 flex items-center justify-center text-lg shrink-0"
            >
              ⚙
            </motion.div>
            <div>
              <h1 className="text-2xl sm:text-3xl font-black font-mono glow-text tracking-tight">
                AUTO<span className="text-green-400">PATCH</span>
              </h1>
              <p className="text-xs text-slate-500 font-mono mt-0.5">
                Self-Improving Multi-Agent Code Repair · LangGraph + Groq
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <StatusBadge status={uiStatus} />
            {runId && (
              <span className="text-xs font-mono text-slate-600 bg-gray-900 px-2 py-1 rounded-lg ring-1 ring-gray-800">
                {runId}
              </span>
            )}
          </div>
        </motion.header>

        {/* ── Controls ─────────────────────────────────────────────────────── */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1, duration: 0.5 }}
          className="bg-gray-900/60 border border-gray-800 rounded-2xl p-5 flex flex-col sm:flex-row sm:items-center gap-5 backdrop-blur-sm"
        >
          <EpisodeSlider value={episodes} onChange={setEpisodes} disabled={running} />
          <div className="sm:ml-auto">
            <RunButton
              running={running}
              onClick={handleRun}
              episode={status.episode || 0}
              totalEpisodes={episodes}
            />
          </div>
        </motion.div>

        {/* ── Stat cards ───────────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <StatCard
            label="Episodes Run"
            value={<AnimatedNumber value={scores.length} />}
            sub={`of ${episodes} requested`}
            accent="bg-green-500/10"
            icon="🎯"
            delay={0.15}
          />
          <StatCard
            label="Avg Score"
            value={<AnimatedNumber value={avg} decimals={3} />}
            sub={scores.length > 0 ? `best ${Math.max(...scores).toFixed(3)}` : 'no data yet'}
            accent="bg-cyan-500/10"
            icon="📊"
            delay={0.2}
          />
          <StatCard
            label="Perfect (1.0)"
            value={<AnimatedNumber value={perfect} />}
            sub={scores.length > 0 ? `${((perfect / scores.length) * 100).toFixed(0)}% perfect rate` : 'no data yet'}
            accent="bg-purple-500/10"
            icon="✨"
            delay={0.25}
          />
        </div>

        {/* ── Chart + Log ──────────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4" style={{ minHeight: 420 }}>

          {/* Reward chart */}
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.3, duration: 0.5 }}
            className="bg-gray-900/60 border border-gray-800 rounded-2xl p-5 flex flex-col backdrop-blur-sm"
            style={{ minHeight: 380 }}
          >
            <RewardChart scores={scores} />
          </motion.div>

          {/* Agent log */}
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.35, duration: 0.5 }}
            className="bg-gray-900/60 border border-gray-800 rounded-2xl p-5 flex flex-col backdrop-blur-sm"
            style={{ minHeight: 380 }}
          >
            <AgentLog logs={logs} />
          </motion.div>
        </div>

        {/* ── Episode mini-timeline ────────────────────────────────────────── */}
        {scores.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
            className="bg-gray-900/60 border border-gray-800 rounded-2xl p-5 backdrop-blur-sm"
          >
            <h2 className="text-sm font-mono font-semibold text-slate-300 tracking-widest uppercase mb-4">
              🗂 Episode Timeline
            </h2>
            <div className="flex flex-wrap gap-2">
              {scores.map((s, i) => {
                const colour =
                  s >= 0.99 ? 'bg-green-500 text-gray-950 shadow-green-500/40' :
                  s >= 0.7  ? 'bg-emerald-600/80 text-white shadow-emerald-600/30' :
                  s >= 0.4  ? 'bg-yellow-600/70 text-white' :
                              'bg-gray-700 text-slate-400'
                return (
                  <motion.div
                    key={i}
                    initial={{ scale: 0, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    transition={{ delay: i * 0.04, type: 'spring', stiffness: 300, damping: 20 }}
                    whileHover={{ scale: 1.15, zIndex: 10 }}
                    className={`relative px-3 py-1.5 rounded-xl text-xs font-mono font-bold shadow-lg cursor-default ${colour}`}
                    title={`Episode ${i + 1}: ${s.toFixed(4)}`}
                  >
                    <span className="text-[10px] opacity-60 mr-1">ep{i + 1}</span>
                    {s.toFixed(2)}
                    {s >= 0.99 && (
                      <motion.span
                        animate={{ rotate: [0, 15, -15, 0] }}
                        transition={{ duration: 1.5, repeat: Infinity }}
                        className="ml-1"
                      >⭐</motion.span>
                    )}
                  </motion.div>
                )
              })}

              {/* ghost pills for remaining episodes */}
              {running && Array.from({ length: episodes - scores.length }).map((_, i) => (
                <motion.div
                  key={`ghost-${i}`}
                  animate={{ opacity: [0.2, 0.5, 0.2] }}
                  transition={{ duration: 1.5, repeat: Infinity, delay: i * 0.1 }}
                  className="px-3 py-1.5 rounded-xl text-xs font-mono text-slate-700 bg-gray-800/50 ring-1 ring-gray-700/50"
                >
                  <span className="text-[10px] mr-1">ep{scores.length + i + 1}</span>…
                </motion.div>
              ))}
            </div>
          </motion.div>
        )}

        {/* ── Footer ───────────────────────────────────────────────────────── */}
        <motion.footer
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.6 }}
          className="text-center text-xs font-mono text-slate-700 pb-2"
        >
          AutoPatch v1.0 · LangGraph · Groq llama-3.3-70b · CodeReview-Env
          <span className="text-slate-800"> · by Thowfiq</span>
        </motion.footer>

      </div>
    </div>
  )
}
