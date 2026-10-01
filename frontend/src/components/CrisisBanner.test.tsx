import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import CrisisBanner from './CrisisBanner'

describe('CrisisBanner', () => {
  it('展示 12355 热线与求助提示', () => {
    render(<CrisisBanner />)
    expect(screen.getByText('请立即寻求帮助')).toBeInTheDocument()
    expect(screen.getByText('12355')).toBeInTheDocument()
  })
})
