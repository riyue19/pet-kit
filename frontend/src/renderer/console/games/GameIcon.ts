import { defineComponent, h, type PropType, type VNode } from 'vue'

/** Black-and-white line icons for the mini games (drawn with currentColor on a 24x24 grid). */
type Shape = { d: string; fill?: boolean } | { c: [number, number, number]; fill?: boolean }

const PIPS: Record<number, [number, number][]> = {
  1: [[12, 12]],
  2: [[8, 8], [16, 16]],
  3: [[8, 8], [12, 12], [16, 16]],
  4: [[8, 8], [16, 8], [8, 16], [16, 16]],
  5: [[8, 8], [16, 8], [12, 12], [8, 16], [16, 16]],
  6: [[8, 7.5], [16, 7.5], [8, 12], [16, 12], [8, 16.5], [16, 16.5]]
}
const die = (n: number): Shape[] => [{ d: 'M5 3.5h14a1.5 1.5 0 0 1 1.5 1.5v14a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V5A1.5 1.5 0 0 1 5 3.5z' }, ...PIPS[n].map(([x, y]): Shape => ({ c: [x, y, 1.5], fill: true }))]
const HEART = 'M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z'

const ICONS: Record<string, Shape[]> = {
  // roulette items
  juice: [{ d: 'M7 8h10l-1.2 13H8.2zM7.5 13h9M12 8V4.5l3.5-1.5' }],
  dice: die(5),
  flash: [{ c: [12, 14.5, 5.5] }, { d: 'M10 9V6h4v3M14 6.5l3.5-2.5M10.5 13l3 3M13.5 13l-3 3' }],
  lens: [{ c: [10.5, 10.5, 6.5] }, { d: 'M15.5 15.5L20.5 20.5' }],
  driver: [{ d: 'M3.5 20.5l1-3.5 8.5-8.5 2.5 2.5L7 19.5zM13 8.5l3.5-5 4 4-5 3.5' }],
  // gestures: a rock, a pair of scissors, a sheet of paper
  rock: [{ d: 'M4.5 15.5l2-6.5 5-3.5 5.5 1.5 2.5 6-3 5.5H8zM9 11l3-1.5M13 15l2.5-1' }],
  scissors: [{ c: [6, 7.5, 2.5] }, { c: [6, 16.5, 2.5] }, { d: 'M8.2 8.8L20.5 17M8.2 15.2L20.5 7' }],
  paper: [{ d: 'M6.5 3h8.5l3 3v15H6.5zM15 3v3h3M9.5 11h6M9.5 15h6' }],
  // dice and the cup they sit under
  die1: die(1),
  die2: die(2),
  die3: die(3),
  die4: die(4),
  die5: die(5),
  die6: die(6),
  cup: [{ d: 'M8 4.5h8l2.5 14.5h-13zM3.5 19h17' }],
  // roulette bits
  heart: [{ d: HEART, fill: true }],
  'heart-empty': [{ d: HEART }],
  bullet: [{ d: 'M9 21V10.5C9 7.5 10.5 5 12 3c1.5 2 3 4.5 3 7.5V21zM9 17h6' }],
  // game tiles
  roulette: [
    { c: [12, 12, 9] },
    { c: [12, 12, 1.4], fill: true },
    ...[0, 1, 2, 3, 4, 5].map((i): Shape => ({ c: [12 + 5.4 * Math.cos((i * Math.PI) / 3), 12 + 5.4 * Math.sin((i * Math.PI) / 3), 1.7] }))
  ],
  rps: [{ c: [6, 7.5, 2.5] }, { c: [6, 16.5, 2.5] }, { d: 'M8.2 8.8L20.5 17M8.2 15.2L20.5 7' }],
  auction: [{ d: 'M6 3h12v18H6zM12 8.5l3 3.5-3 3.5-3-3.5zM8.5 5.5h1M14.5 18.5h1' }],
  // a rod, its line and float, and the water
  fishing: [{ d: 'M4 20L15 3.5M15 3.5c3 1 4.5 4 4.5 8.5M3 21.5q2.5-2 5 0t5 0 5 0 3-1' }, { c: [19.5, 14.5, 1.8], fill: true }],
  taboo: [{ d: 'M4 4.5h16v11.5H10l-4.5 4v-4H4zM8.5 7.5l7 5.5M15.5 7.5l-7 5.5' }]
}

export const GameIcon = defineComponent({
  name: 'GameIcon',
  props: {
    name: { type: String, required: true },
    size: { type: [Number, String] as PropType<number | string>, default: 24 }
  },
  setup(props) {
    return (): VNode =>
      h(
        'svg',
        {
          viewBox: '0 0 24 24',
          width: props.size,
          height: props.size,
          fill: 'none',
          stroke: 'currentColor',
          'stroke-width': 1.6,
          'stroke-linecap': 'round',
          'stroke-linejoin': 'round',
          style: 'display: inline-block; vertical-align: middle; flex: none'
        },
        (ICONS[props.name] ?? []).map((s) =>
          'd' in s
            ? h('path', { d: s.d, fill: s.fill ? 'currentColor' : 'none' })
            : h('circle', { cx: s.c[0], cy: s.c[1], r: s.c[2], fill: s.fill ? 'currentColor' : 'none' })
        )
      )
  }
})
