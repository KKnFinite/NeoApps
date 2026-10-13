/* Position the existing canonical Reports-To forest; never derive relationships. */
(() => {
    'use strict';

    function layoutForest(sources, minimumWidth = 0) {
        const padding = 24, siblingGap = 24, rootGap = 40, levelGap = 48;
        const heights = [];
        const measure = (source, depth) => {
            const children = (source.children || []).map(child => measure(child, depth + 1));
            const cardWidth = source.width, cardHeight = source.height;
            heights[depth] = Math.max(heights[depth] || 0, cardHeight);
            const childWidth = children.reduce((sum, child) => sum + child.width, 0)
                + Math.max(0, children.length - 1) * siblingGap;
            return {source, depth, children, cardWidth, cardHeight, childWidth, width: Math.max(cardWidth, childWidth)};
        };
        const roots = sources.map(source => measure(source, 0));
        const forestWidth = roots.reduce((sum, root) => sum + root.width, 0)
            + Math.max(0, roots.length - 1) * rootGap;
        const width = Math.max(minimumWidth, forestWidth + padding * 2);
        const rows = [], nodes = [], links = [];
        let height = padding;
        heights.forEach((rowHeight, depth) => {
            rows[depth] = height;
            height += rowHeight + (depth < heights.length - 1 ? levelGap : 0);
        });
        const place = (node, left, parent = null) => {
            const positioned = {source: node.source, parent, depth: node.depth,
                x: left + node.width / 2 - node.cardWidth / 2, y: rows[node.depth],
                width: node.cardWidth, height: node.cardHeight};
            nodes.push(positioned);
            let childLeft = left + (node.width - node.childWidth) / 2;
            const children = node.children.map(child => {
                const result = place(child, childLeft, positioned);
                childLeft += child.width + siblingGap;
                return result;
            });
            if (children.length) {
                const x = positioned.x + positioned.width / 2;
                const bus = rows[node.depth + 1] - levelGap / 2;
                const centers = children.map(child => child.x + child.width / 2);
                const path = [`M ${x} ${positioned.y + positioned.height} V ${bus}`,
                    `M ${centers[0]} ${bus} H ${centers[centers.length - 1]}`,
                    ...children.map((child, index) => `M ${centers[index]} ${bus} V ${child.y}`)].join(' ');
                links.push({parent: positioned, children, path});
            }
            return positioned;
        };
        let left = (width - forestWidth) / 2;
        roots.forEach(root => { place(root, left); left += root.width + rootGap; });
        return {width, height: height + padding, nodes, links};
    }

    if (typeof module !== 'undefined') module.exports = {layoutForest};
    if (typeof document === 'undefined') return;

    document.querySelectorAll('[data-org-chart-viewport]').forEach(viewport => {
        const canvas = viewport.querySelector('[data-org-forest]');
        if (!canvas) return;
        const svg = canvas.querySelector('[data-org-connectors]');
        const read = list => [...list.children].map(element => {
            const card = element.querySelector(':scope > .staffing-reporting-card');
            const children = element.querySelector(':scope > .staffing-reporting-branches');
            const bounds = card.getBoundingClientRect();
            return {element, card, width: bounds.width, height: bounds.height,
                children: children ? read(children) : []};
        });
        let frame = null, first = true, previousWidth = 0;
        const draw = () => {
            frame = null;
            const forest = read(canvas.querySelector(':scope > .staffing-reporting-branches'));
            const layout = layoutForest(forest, viewport.clientWidth);
            canvas.style.width = `${layout.width}px`;
            canvas.style.height = `${layout.height}px`;
            layout.nodes.forEach(node => {
                // Nested list semantics stay intact; offsets are relative to
                // the actual Reports-To parent, never an assignment or tier.
                node.source.element.style.left = `${node.x - (node.parent?.x || 0)}px`;
                node.source.element.style.top = `${node.y - (node.parent?.y || 0)}px`;
            });
            svg.setAttribute('width', layout.width);
            svg.setAttribute('height', layout.height);
            svg.setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`);
            svg.replaceChildren(...layout.links.map(link => {
                const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                path.setAttribute('d', link.path);
                path.dataset.reportingParent = link.parent.source.element.dataset.reportingPerson;
                path.dataset.reportingChildren = link.children.map(child => child.source.element.dataset.reportingPerson).join(' ');
                return path;
            }));
            canvas.dataset.orgLayout = 'ready';
            if (first) {
                const selected = layout.nodes.find(node => node.source.card.hasAttribute('aria-current'));
                // A wide forest can have empty space between its roots.
                // Start on the selected employee or first canonical root.
                const anchor = selected || layout.nodes.find(node => node.parent === null);
                viewport.scrollLeft = anchor
                    ? anchor.x + anchor.width / 2 - viewport.clientWidth / 2 : 0;
                if (selected) viewport.scrollTop = Math.max(0, selected.y - 24);
                first = false;
            }
            previousWidth = viewport.clientWidth;
        };
        const schedule = () => {
            if (frame === null) frame = requestAnimationFrame(draw);
        };
        schedule();
        document.fonts?.ready.then(schedule);
        if (typeof ResizeObserver !== 'undefined') {
            const observer = new ResizeObserver(() => {
                if (viewport.clientWidth !== previousWidth) schedule();
            });
            observer.observe(viewport);
        } else window.addEventListener('resize', schedule);
    });
})();
