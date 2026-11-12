export interface SpeechRecognitionEventLike {
  resultIndex: number
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>
}

export interface SpeechUpdate {
  text: string
  isFinal: boolean
  utteranceId: string
}

/** One replaceable interim hypothesis, followed by exactly-once final results. */
export class SpeechResultTracker {
  private sequence = 0
  private ids = new Map<number, string>()
  private finalized = new Set<number>()
  private lastInterim = ''
  private finalCaption = ''

  resetCycle() {
    this.ids.clear()
    this.finalized.clear()
    this.lastInterim = ''
  }

  clearCaption() {
    this.finalCaption = ''
  }

  private id(index: number) {
    if (!this.ids.has(index)) this.ids.set(index, `browser_${++this.sequence}`)
    return this.ids.get(index)!
  }

  read(event: SpeechRecognitionEventLike) {
    const updates: SpeechUpdate[] = []
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i]
      const text = result[0].transcript.trim()
      if (!text || !result.isFinal || this.finalized.has(i)) continue
      this.finalized.add(i)
      this.finalCaption = text
      updates.push({ text, isFinal: true, utteranceId: this.id(i) })
    }

    const partials: string[] = []
    let first = -1
    for (let i = 0; i < event.results.length; i++) {
      const result = event.results[i]
      const text = result[0].transcript.trim()
      if (result.isFinal || this.finalized.has(i) || !text) continue
      if (first === -1) first = i
      partials.push(text)
    }
    const interim = partials.join(' ')
    const signature = first === -1 ? '' : `${this.id(first)}:${interim}`
    if (signature && signature !== this.lastInterim) {
      updates.push({ text: interim, isFinal: false, utteranceId: this.id(first) })
    }
    this.lastInterim = signature
    return { updates, interim, caption: interim || this.finalCaption }
  }
}
