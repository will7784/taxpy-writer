/**
 * File Explorer — Tree-view interactivo para Impuestia Admin
 */

document.addEventListener('DOMContentLoaded', function () {
    const treeContainers = document.querySelectorAll('.file-tree');

    treeContainers.forEach(function (container) {
        container.addEventListener('click', function (e) {
            const toggle = e.target.closest('.tree-toggle');
            if (!toggle) return;

            const node = toggle.closest('.tree-node');
            const children = node.querySelector('.tree-children');
            if (!children) return;

            const isOpen = children.style.display !== 'none';
            if (isOpen) {
                children.style.display = 'none';
                toggle.textContent = '▶';
            } else {
                children.style.display = 'block';
                toggle.textContent = '▼';
            }
        });
    });
});
