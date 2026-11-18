const palettes = [
  ['#ffc744', '#ff615c', '#24377d'],
  ['#56cbef', '#2262b7', '#fffba4'],
  ['#f990a0', '#fff2cc', '#da3b52'],
  ['#a7dd91', '#175f5a', '#ffed68'],
  ['#bb9bf2', '#6538af', '#ffe575'],
  ['#ff995f', '#a03e68', '#fff1bf'],
];

export function makeSticker(id) {
  let seed = 2166136261;
  for (const letter of id) seed = Math.imul(seed ^ letter.charCodeAt(0), 16777619);
  seed >>>= 0;

  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  canvas.width = canvas.height = 128;
  const paint = canvas.getContext('2d');
  const [base, main, accent] = palettes[seed % palettes.length];
  paint.fillStyle = base;
  paint.fillRect(0, 0, 128, 128);
  paint.translate(64, 64);
  paint.rotate(((seed >>> 8) % 31 - 15) * Math.PI / 180);
  paint.lineCap = 'round';

  function dot(x, y, radius, color) {
    paint.fillStyle = color;
    paint.beginPath();
    paint.arc(x, y, radius, 0, 2 * Math.PI);
    paint.fill();
  }

  switch ((seed >>> 5) % 4) {
    case 0:
      for (let petal = 0; petal < 8; petal++) {
        const angle = petal * Math.PI / 4;
        dot(Math.cos(angle) * 25, Math.sin(angle) * 25, 15, main);
      }
      dot(0, 0, 16, accent);
      break;
    case 1:
      for (const radius of [52, 37, 22]) {
        paint.strokeStyle = main;
        paint.lineWidth = 8;
        paint.beginPath();
        paint.arc(5, 9, radius, .75 * Math.PI, 1.85 * Math.PI);
        paint.stroke();
      }
      dot(24, 20, 15, accent);
      break;
    case 2:
      paint.fillStyle = main;
      paint.beginPath();
      for (let point = 0; point < 16; point++) {
        const angle = point * Math.PI / 8;
        const radius = point % 2 ? 18 : 47;
        paint.lineTo(Math.cos(angle) * radius, Math.sin(angle) * radius);
      }
      paint.fill();
      dot(0, 0, 10, accent);
      break;
    default:
      paint.lineWidth = 12;
      for (const y of [-30, 0, 30]) {
        paint.strokeStyle = y === 0 ? accent : main;
        paint.beginPath();
        paint.moveTo(-65, y);
        paint.bezierCurveTo(-24, y - 45, 18, y + 45, 65, y);
        paint.stroke();
      }
  }
  return canvas;
}
