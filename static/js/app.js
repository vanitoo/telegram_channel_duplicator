document.addEventListener('DOMContentLoaded', function() {
    const statusIndicator = document.getElementById('status-indicator');
    const statusText = document.getElementById('status-text');
    const toggleBtn = document.getElementById('toggle-btn');
    const channelsTable = document.getElementById('channels-table').querySelector('tbody');
    const addChannelForm = document.getElementById('add-channel-form');

    // Инициализация графика
    const statsChart = new Chart(
        document.getElementById('stats-chart'),
        {
            type: 'doughnut',
            data: {
                labels: ['Успешно', 'Ошибки'],
                datasets: [{
                    data: [0, 0],
                    backgroundColor: ['#4bc0c0', '#ff6384']
                }]
            }
        }
    );

    // Обновление статуса
    function updateStatus() {
        fetch('/api/status')
            .then(response => response.json())
            .then(data => {
                statusIndicator.style.backgroundColor = data.status === 'running' ? 'green' : 'red';
                statusText.textContent = data.status === 'running' ? 'Система работает' : 'Система приостановлена';
                toggleBtn.textContent = data.status === 'running' ? 'Приостановить' : 'Возобновить';

                // Обновление графика
                statsChart.data.datasets[0].data = [data.stats.success_count, data.stats.error_count];
                statsChart.update();
            });
    }

    // Обновление списка каналов
    function updateChannelsList() {
        fetch('/api/channels')
            .then(response => response.json())
            .then(data => {
                channelsTable.innerHTML = '';
                data.channels.forEach(channel => {
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td>${channel.source}</td>
                        <td>${channel.target}</td>
                        <td>${channel.is_active ? 'Активен' : 'Неактивен'}</td>
                        <td>
                            <button class="btn btn-sm btn-danger delete-btn" data-source="${channel.source}">
                                Удалить
                            </button>
                        </td>
                    `;
                    channelsTable.appendChild(row);
                });

                // Назначение обработчиков для кнопок удаления
                document.querySelectorAll('.delete-btn').forEach(btn => {
                    btn.addEventListener('click', function() {
                        const source = this.getAttribute('data-source');
                        if (confirm(`Удалить пару каналов ${source}?`)) {
                            fetch(`/api/channels/${source}`, {method: 'DELETE'})
                                .then(() => updateChannelsList());
                        }
                    });
                });
            });
    }

    // Обработчик кнопки паузы/возобновления
    toggleBtn.addEventListener('click', function() {
        const action = this.textContent === 'Приостановить' ? 'pause' : 'resume';
        fetch(`/api/${action}`, {method: 'POST'})
            .then(() => updateStatus());
    });

    // Обработчик формы добавления канала
    addChannelForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const source = document.getElementById('source-channel').value;
        const target = document.getElementById('target-channel').value;

        fetch('/api/channels', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({source, target})
        }).then(() => {
            updateChannelsList();
            this.reset();
        });
    });

    // Первоначальная загрузка данных
    updateStatus();
    updateChannelsList();

    // Обновление данных каждые 5 секунд
    setInterval(updateStatus, 5000);
});
