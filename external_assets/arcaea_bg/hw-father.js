window.addEventListener('load', () => {
  /** @type {NodeListOf<HTMLElement>} */
  const elems = document.querySelectorAll('.hw-father')
  elems.forEach((elem) => {
    const parent = elem.parentElement
    /** @type {string[]} */
    const paddingTops = []
    /** @type {string[]} */
    const paddingLefts = []

    const { paddingTop, paddingLeft } = window.getComputedStyle(parent)
    paddingTops.push(paddingTop)
    paddingLefts.push(paddingLeft)

    if ('additionalMt' in elem.dataset) paddingTops.push(elem.dataset.additionalMt)
    if ('additionalMl' in elem.dataset) paddingLefts.push(elem.dataset.additionalMl)

    const { width, height } = parent.getBoundingClientRect()
    elem.style.width = `${width}px`
    elem.style.height = `${height}px`
    elem.style.marginLeft = `calc((${paddingLefts.join(' + ')}) * -1)`
    elem.style.marginTop = `calc((${paddingTops.join(' + ')}) * -1)`
  })
})
