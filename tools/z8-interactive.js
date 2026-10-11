// SPDX-License-Identifier: GPL-2.0-or-later
for (const button of document.querySelectorAll('[data-key]')) {
  button.addEventListener('click', async () => {
    const response = await fetch('/key/' + button.dataset.key, {method: 'POST'});
    if (!response.ok) document.getElementById('status').textContent = '按键未接受：' + response.status;
  });
}
document.getElementById('stop').addEventListener('click', async () => {
  const response = await fetch('/stop', {method: 'POST'});
  if (response.ok) document.getElementById('status').textContent = '正在停止仿真';
});
async function update() {
  try {
    const status = await (await fetch('/status')).json();
    document.getElementById('status').textContent = JSON.stringify(status, null, 2);
    const blob = await (await fetch('/frame')).blob();
    if (blob.size) {
      const frame = document.getElementById('frame');
      const previous = frame.src;
      frame.src = URL.createObjectURL(blob);
      if (previous.startsWith('blob:')) URL.revokeObjectURL(previous);
    }
  } catch (error) {
    document.getElementById('status').textContent = '仿真连接已关闭';
  }
  setTimeout(update, 500);
}
update();
