(function (global) {
  'use strict';

  const escapeXml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&apos;'
  })[char]);

  const number = value => {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  function render(target, options) {
    const container = typeof target === 'string' ? document.getElementById(target) : target;
    if (!container) return;

    const labels = options.labels || [];
    const datasets = (options.datasets || []).map(dataset => ({
      ...dataset,
      data: labels.map((_, index) => number((dataset.data || [])[index]))
    }));
    const values = datasets.flatMap(dataset => dataset.data.filter(value => value !== null));
    if (!labels.length || !values.length) {
      container.innerHTML = '<div class="svg-chart-empty">暂无图表数据</div>';
      return;
    }

    const width = 900;
    const height = Number(options.height) || 300;
    const margin = {
      left: 58,
      right: 22,
      top: options.title ? 54 : 34,
      bottom: 42
    };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const rawMin = Math.min(...values);
    const rawMax = Math.max(...values);
    const padding = Math.max((rawMax - rawMin) * 0.1, Math.abs(rawMax || 1) * 0.005, 0.001);
    const yMin = options.yMin === undefined ? rawMin - padding : Number(options.yMin);
    const yMax = options.yMax === undefined ? rawMax + padding : Number(options.yMax);
    const x = index => margin.left + (labels.length === 1 ? plotWidth / 2 : index * plotWidth / (labels.length - 1));
    const y = value => margin.top + (1 - (value - yMin) / (yMax - yMin || 1)) * plotHeight;
    const formatY = options.formatY || (value => Number(value).toFixed(4));

    const yGrid = Array.from({length: 5}, (_, index) => {
      const ratio = index / 4;
      const value = yMax - (yMax - yMin) * ratio;
      const gridY = margin.top + plotHeight * ratio;
      return `<line class="svg-chart-grid" x1="${margin.left}" y1="${gridY}" x2="${width - margin.right}" y2="${gridY}"/>
        <text class="svg-chart-axis" x="${margin.left - 8}" y="${gridY + 4}" text-anchor="end">${escapeXml(formatY(value))}</text>`;
    }).join('');

    const maxXTicks = Math.min(Number(options.maxXTicks) || 7, labels.length);
    const tickIndexes = [...new Set(Array.from({length: maxXTicks}, (_, index) =>
      Math.round(index * (labels.length - 1) / Math.max(1, maxXTicks - 1))
    ))];
    const xGrid = tickIndexes.map(index => `
      <line class="svg-chart-grid svg-chart-grid-x" x1="${x(index)}" y1="${margin.top}" x2="${x(index)}" y2="${margin.top + plotHeight}"/>
      <text class="svg-chart-axis" x="${x(index)}" y="${height - 13}" text-anchor="middle">${escapeXml(labels[index])}</text>
    `).join('');

    const seriesSvg = datasets.map((dataset, datasetIndex) => {
      const segments = [];
      let current = [];
      dataset.data.forEach((value, index) => {
        if (value === null) {
          if (current.length) segments.push(current);
          current = [];
        } else {
          current.push({index, value});
        }
      });
      if (current.length) segments.push(current);

      const paths = dataset.showLine === false ? '' : segments.map(segment => {
        const d = segment.map((point, index) =>
          `${index ? 'L' : 'M'} ${x(point.index).toFixed(2)} ${y(point.value).toFixed(2)}`
        ).join(' ');
        const dash = dataset.dash ? `stroke-dasharray="${dataset.dash}"` : '';
        return `<path class="svg-chart-line" d="${d}" stroke="${escapeXml(dataset.color || '#2563eb')}" ${dash}/>`;
      }).join('');

      const points = dataset.data.map((value, index) => {
        if (value === null) return '';
        const configuredRadius = Array.isArray(dataset.pointRadius)
          ? dataset.pointRadius[index]
          : dataset.pointRadius;
        const visibleRadius = Number(configuredRadius ?? (labels.length <= 12 ? 3 : 0));
        const pointColor = Array.isArray(dataset.pointColors)
          ? dataset.pointColors[index]
          : (dataset.pointColor || dataset.color || '#2563eb');
        const tooltip = options.tooltip
          ? options.tooltip({label: labels[index], value, dataset, datasetIndex, index})
          : `${dataset.label || ''} ${labels[index]}：${formatY(value)}`;
        return `<circle cx="${x(index)}" cy="${y(value)}" r="${Math.max(visibleRadius, 7)}"
          fill="${escapeXml(pointColor)}" fill-opacity="${visibleRadius ? 1 : 0}"
          stroke="${escapeXml(pointColor)}" stroke-opacity="${visibleRadius ? 1 : 0}"
          class="svg-chart-point"><title>${escapeXml(tooltip)}</title></circle>`;
      }).join('');
      return paths + points;
    }).join('');

    const annotations = (options.annotations || []).map(annotation => {
      const value = number(annotation.value);
      if (value === null || annotation.index < 0 || annotation.index >= labels.length) return '';
      const pointX = x(annotation.index);
      const pointY = y(value);
      const anchor = pointX > width - 150 ? 'end' : 'start';
      const textX = pointX + (anchor === 'end' ? -8 : 8);
      return `<g class="svg-chart-annotation">
        <circle cx="${pointX}" cy="${pointY}" r="4" fill="${escapeXml(annotation.color)}"/>
        <text x="${textX}" y="${pointY - 9}" text-anchor="${anchor}" fill="${escapeXml(annotation.color)}">${escapeXml(annotation.label)}</text>
      </g>`;
    }).join('');

    const legend = datasets.map((dataset, index) => {
      const legendX = margin.left + index * Math.min(210, plotWidth / Math.max(1, datasets.length));
      const dash = dataset.dash ? `stroke-dasharray="${dataset.dash}"` : '';
      return `<g transform="translate(${legendX},${margin.top - 17})">
        <line x1="0" y1="0" x2="24" y2="0" stroke="${escapeXml(dataset.color || '#2563eb')}" stroke-width="2.5" ${dash}/>
        <text class="svg-chart-legend" x="31" y="4">${escapeXml(dataset.label || '')}</text>
      </g>`;
    }).join('');

    container.innerHTML = `<svg class="svg-line-chart" viewBox="0 0 ${width} ${height}" role="img"
      aria-label="${escapeXml(options.ariaLabel || options.title || '折线图')}" xmlns="http://www.w3.org/2000/svg">
      <style>
        .svg-chart-grid{stroke:#e2e8f0;stroke-width:1}.svg-chart-grid-x{stroke:#edf2f7}
        .svg-chart-axis{font:11px system-ui,sans-serif;fill:#64748b}
        .svg-chart-title{font:600 14px system-ui,sans-serif;fill:#334155}
        .svg-chart-legend{font:12px system-ui,sans-serif;fill:#475569}
        .svg-chart-line{fill:none;stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round;vector-effect:non-scaling-stroke}
        .svg-chart-point{cursor:crosshair}.svg-chart-annotation text{font:600 12px system-ui,sans-serif}
      </style>
      ${options.title ? `<text class="svg-chart-title" x="${width / 2}" y="19" text-anchor="middle">${escapeXml(options.title)}</text>` : ''}
      ${legend}${yGrid}${xGrid}${seriesSvg}${annotations}
      ${options.yLabel ? `<text class="svg-chart-axis" x="15" y="${margin.top + plotHeight / 2}" text-anchor="middle"
        transform="rotate(-90 15 ${margin.top + plotHeight / 2})">${escapeXml(options.yLabel)}</text>` : ''}
    </svg>`;
  }

  global.SvgLineChart = Object.freeze({render});
})(window);
