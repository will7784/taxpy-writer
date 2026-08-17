/**
 * Drag & Drop Upload — Impuestia Admin
 */

document.addEventListener('DOMContentLoaded', function () {
    const uploadZone = document.getElementById('upload-zone');
    const fileInput = document.getElementById('file-input');
    const progress = document.getElementById('upload-progress');
    const progressBar = document.getElementById('upload-progress-bar');
    const uploadResult = document.getElementById('upload-result');

    if (!uploadZone || !fileInput) return;

    uploadZone.addEventListener('click', function () {
        fileInput.click();
    });

    uploadZone.addEventListener('dragover', function (e) {
        e.preventDefault();
        uploadZone.classList.add('drag-over');
    });

    uploadZone.addEventListener('dragleave', function () {
        uploadZone.classList.remove('drag-over');
    });

    uploadZone.addEventListener('drop', function (e) {
        e.preventDefault();
        uploadZone.classList.remove('drag-over');
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFiles(files);
        }
    });

    fileInput.addEventListener('change', function () {
        if (fileInput.files.length > 0) {
            handleFiles(fileInput.files);
        }
    });

    function handleFiles(files) {
        const cliente = uploadZone.dataset.cliente || '';
        const subfolder = uploadZone.dataset.subfolder || '';

        progress.classList.add('active');
        progressBar.style.width = '0%';

        let completed = 0;
        const total = files.length;

        Array.from(files).forEach(function (file) {
            uploadFile(file, cliente, subfolder, function (success, result) {
                completed++;
                progressBar.style.width = ((completed / total) * 100) + '%';
                if (completed >= total) {
                    setTimeout(function () {
                        progress.classList.remove('active');
                        progressBar.style.width = '0%';
                        if (uploadResult) {
                            uploadResult.innerHTML = '<div class="alert alert-success">' + total + ' archivo(s) procesado(s).</div>';
                        }
                        setTimeout(function () { location.reload(); }, 1200);
                    }, 500);
                }
            });
        });
    }

    function uploadFile(file, cliente, subfolder, callback) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('cliente', cliente);
        formData.append('subfolder', subfolder);

        fetch('/api/files/upload', {
            method: 'POST',
            body: formData,
        })
        .then(function (resp) { return resp.json(); })
        .then(function (json) { callback(true, json); })
        .catch(function (err) { callback(false, err.message); });
    }
});
