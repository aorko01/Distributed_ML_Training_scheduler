import { useEffect, useRef, useState } from 'react'
import { mockWorker } from '../data/mock'

const HISTORY_LEN = 48
const TICK_MS = 1500

interface Target {
  cpuLoad: number
  memUsage: number
  gpuLoad: number
  vramUsedGb: number
  gpuTempC: number
}

const BASELINE: Target = {
  cpuLoad: 38,
  memUsage: 46,
  gpuLoad: 12,
  vramUsedGb: 2.1,
  gpuTempC: 42
}

const IDLE: Target = {
  cpuLoad: 1.5,
  memUsage: 22,
  gpuLoad: 0,
  vramUsedGb: 0.4,
  gpuTempC: 33
}

export interface Metrics {
  cpuLoad: number
  memUsage: number
  gpuLoad: number
  vramUsedGb: number
  vramFreeGb: number
  gpuTempC: number
  cpuHistory: number[]
  lastHeartbeat: string
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function drift(current: number, volatility: number, target: number): number {
  const next = current + (Math.random() - 0.5) * volatility
  const pull = (target - next) * 0.12
  return clamp(next + pull, 0, 100)
}

function initialMetrics(): Metrics {
  return {
    cpuLoad: BASELINE.cpuLoad,
    memUsage: BASELINE.memUsage,
    gpuLoad: BASELINE.gpuLoad,
    vramUsedGb: BASELINE.vramUsedGb,
    vramFreeGb: mockWorker.gpuVramTotalGb - BASELINE.vramUsedGb,
    gpuTempC: BASELINE.gpuTempC,
    cpuHistory: Array.from({ length: HISTORY_LEN }, (_, i) => {
      const progress = i / HISTORY_LEN
      return Math.round((BASELINE.cpuLoad * progress + Math.random() * 8) * 10) / 10
    }),
    lastHeartbeat: new Date().toLocaleTimeString()
  }
}

export function useSimulation(connected: boolean): Metrics {
  const target = connected ? BASELINE : IDLE
  const [metrics, setMetrics] = useState<Metrics>(initialMetrics)
  const heartbeatRef = useRef<number>(0)

  useEffect(() => {
    const interval = window.setInterval(() => {
      setMetrics((prev) => {
        const cpuLoad = drift(prev.cpuLoad, 14, target.cpuLoad)
        const memUsage = drift(prev.memUsage, 4, target.memUsage)
        const gpuLoad = drift(prev.gpuLoad, 26, target.gpuLoad)
        const vramUsedGb = clamp(
          prev.vramUsedGb + (Math.random() - 0.5) * 0.4 + (target.vramUsedGb - prev.vramUsedGb) * 0.08,
          0,
          mockWorker.gpuVramTotalGb
        )
        const gpuTempC = clamp(prev.gpuTempC + (Math.random() - 0.5) * 2 + (target.gpuTempC - prev.gpuTempC) * 0.1, 25, 95)
        const cpuHistory = [...prev.cpuHistory.slice(-(HISTORY_LEN - 1)), Math.round(cpuLoad * 10) / 10]

        const heartbeat = ++heartbeatRef.current % 4 === 0
        return {
          cpuLoad: Math.round(cpuLoad * 10) / 10,
          memUsage: Math.round(memUsage * 10) / 10,
          gpuLoad: Math.round(gpuLoad * 10) / 10,
          vramUsedGb: Math.round(vramUsedGb * 10) / 10,
          vramFreeGb: Math.round((mockWorker.gpuVramTotalGb - vramUsedGb) * 10) / 10,
          gpuTempC: Math.round(gpuTempC),
          cpuHistory,
          lastHeartbeat: heartbeat ? new Date().toLocaleTimeString() : prev.lastHeartbeat
        }
      })
    }, TICK_MS)

    return () => window.clearInterval(interval)
  }, [target.cpuLoad, target.memUsage, target.gpuLoad, target.vramUsedGb, target.gpuTempC])

  return metrics
}
