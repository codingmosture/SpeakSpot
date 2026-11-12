import { useState } from 'react'

interface Props {
  canSearch: boolean
  onSubmitText: (text: string) => void
  autoFollow: boolean
  onAutoFollowChange: (value: boolean) => void
}

export function ControlPanel({ canSearch, onSubmitText, autoFollow, onAutoFollowChange }: Props) {
  const [text, setText] = useState('')
  return (
    <section className="search-panel" aria-labelledby="search-title">
      <h2 id="search-title">문서에서 찾기</h2>
      <form
        className="text-form"
        onSubmit={(e) => {
          e.preventDefault()
          const value = text.trim()
          if (!value || !canSearch) return
          onSubmitText(value)
          setText('')
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="찾을 내용을 입력하세요"
          aria-label="찾을 내용"
        />
        <button type="submit" className="btn primary" disabled={!text.trim() || !canSearch}>
          찾기
        </button>
      </form>
      <label className="toggle follow">
        <input
          type="checkbox"
          checked={autoFollow}
          onChange={(e) => onAutoFollowChange(e.target.checked)}
        />
        매칭 위치로 자동 페이지 이동
      </label>
    </section>
  )
}
