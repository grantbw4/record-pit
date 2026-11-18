const GRAVITY = 1450;
const STEP = 1 / 90;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

export class Pit {
  constructor(arena, { select, reducedMotion }) {
    this.arena = arena;
    this.select = select;
    this.reducedMotion = reducedMotion;
    this.paused = reducedMotion;
    this.balls = [];
    this.frame = null;
    this.lastTime = null;
    this.accumulator = 0;
    this.quietFor = 0;
    this.stepFrame = this.stepFrame.bind(this);

    new ResizeObserver(() => {
      const { clientWidth, clientHeight } = arena;
      if (clientWidth === this.width && clientHeight === this.height) return;
      this.layout(false);
    }).observe(arena);
  }

  setBalls(items, nodes) {
    this.drag = null;
    this.balls = items.map((item, index) => ({
      item,
      node: nodes[index],
      index,
      radius: 0,
      x: 0,
      y: 0,
      vx: 0,
      vy: 0,
    }));
    this.balls.forEach(ball => this.bindPointer(ball));
    this.layout(!this.paused);
  }

  layout(fall) {
    if (!this.balls.length) return;
    this.width = this.arena.clientWidth;
    this.height = this.arena.clientHeight;
    if (!this.width || !this.height) return;
    this.drag = null;
    const area = this.width * this.height;
    const total = this.balls.reduce((sum, ball) => sum + ball.item.seconds, 0);
    const scale = Math.min(
      Math.sqrt(area * .51 / (Math.PI * total)),
      this.width * .20 / Math.sqrt(this.balls[0].item.seconds),
    );

    for (const ball of this.balls) {
      const radius = scale * Math.sqrt(ball.item.seconds);
      const spread = (ball.index * .61803398875 + .42) % 1;
      ball.radius = radius;
      ball.entered = !fall;
      ball.inverseMass = 1 / (radius * radius);
      ball.x = radius + spread * (this.width - 2 * radius);
      ball.y = fall
        ? -radius - (ball.index % 19) * 7 - Math.floor(ball.index / 19) * 23
        : radius + ((ball.index * .38196601125) % 1) * (this.height - 2 * radius);
      ball.vx = fall ? (spread - .5) * 175 : 0;
      ball.vy = fall ? 95 : 0;
      const diameter = radius * 2;
      ball.node.style.setProperty('--diameter', diameter + 'px');
    }

    if (!fall) {
      for (let i = 0; i < 165; i++) this.integrate(STEP);
    }
    this.draw();
    if (!this.paused && (fall || this.hasMovingBalls())) this.wake();
  }

  drop() {
    if (this.reducedMotion) {
      this.layout(false);
      return;
    }
    this.paused = false;
    this.layout(true);
  }

  setPaused(paused) {
    this.paused = paused;
    if (!paused) this.wake();
  }

  wake() {
    this.quietFor = 0;
    if (this.paused || this.frame !== null) return;
    this.lastTime = null;
    this.accumulator = 0;
    this.frame = requestAnimationFrame(this.stepFrame);
  }

  stepFrame(time) {
    this.frame = null;
    if (this.paused) return;

    const elapsed = this.lastTime === null ? STEP : Math.min(.034, (time - this.lastTime) / 1000);
    this.lastTime = time;
    this.accumulator += elapsed;
    while (this.accumulator >= STEP) {
      this.integrate(STEP);
      this.accumulator -= STEP;
    }

    this.draw();
    this.quietFor = !this.drag && !this.hasMovingBalls() ? this.quietFor + elapsed : 0;
    if (this.quietFor < .75) this.frame = requestAnimationFrame(this.stepFrame);
  }

  hasMovingBalls() {
    return this.balls.some(ball => ball.y < ball.radius || Math.hypot(ball.vx, ball.vy) > 22);
  }

  integrate(dt) {
    for (const ball of this.balls) {
      if (this.drag?.ball === ball) continue;
      ball.vy = (ball.vy + GRAVITY * dt) * .995;
      ball.vx *= .994;
      ball.x += ball.vx * dt;
      ball.y += ball.vy * dt;
    }

    for (let pass = 0; pass < 5; pass++) {
      for (let i = 0; i < this.balls.length; i++) {
        const ball = this.balls[i];
        for (let j = i + 1; j < this.balls.length; j++) this.collide(ball, this.balls[j]);
      }
      for (const ball of this.balls) this.contain(ball);
    }
  }

  collide(a, b) {
    const minimum = a.radius + b.radius + .5;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    if (Math.abs(dx) >= minimum || Math.abs(dy) >= minimum) return;
    const distance2 = dx * dx + dy * dy;
    if (distance2 >= minimum * minimum) return;

    const distance = Math.sqrt(distance2);
    const nx = distance ? dx / distance : 1;
    const ny = distance ? dy / distance : 0;
    const massA = this.drag?.ball === a ? 0 : a.inverseMass;
    const massB = this.drag?.ball === b ? 0 : b.inverseMass;
    const totalMass = massA + massB;
    const overlap = (minimum - distance) / totalMass * .85;
    a.x -= nx * overlap * massA;
    a.y -= ny * overlap * massA;
    b.x += nx * overlap * massB;
    b.y += ny * overlap * massB;

    const closingSpeed = (b.vx - a.vx) * nx + (b.vy - a.vy) * ny;
    if (closingSpeed >= 0) return;
    const rebound = closingSpeed < -65 ? .44 : 0;
    const impulse = -(1 + rebound) * closingSpeed / totalMass;
    a.vx -= nx * impulse * massA;
    a.vy -= ny * impulse * massA;
    b.vx += nx * impulse * massB;
    b.vy += ny * impulse * massB;
  }

  contain(ball) {
    const { radius } = ball;
    if (ball.y >= radius) ball.entered = true;
    if (ball.entered && ball.y < radius) {
      ball.y = radius;
      if (ball.vy < 0) ball.vy *= -.62;
    }
    if (ball.x < radius) {
      ball.x = radius;
      if (ball.vx < 0) ball.vx *= -.62;
    } else if (ball.x > this.width - radius) {
      ball.x = this.width - radius;
      if (ball.vx > 0) ball.vx *= -.62;
    }
    if (ball.y > this.height - radius) {
      ball.y = this.height - radius;
      if (ball.vy > 0) ball.vy = ball.vy > 60 ? ball.vy * -.57 : 0;
      ball.vx *= .91;
    }
  }

  draw() {
    for (const ball of this.balls) {
      const x = (ball.x - ball.radius).toFixed(2);
      const y = (ball.y - ball.radius).toFixed(2);
      ball.node.style.transform = 'translate(' + x + 'px, ' + y + 'px)';
    }
  }

  bindPointer(ball) {
    const node = ball.node;
    let suppressClick = false;
    node.addEventListener('pointerdown', event => {
      if (event.button !== 0 || this.drag || this.paused) return;
      suppressClick = false;
      this.drag = {
        ball,
        startX: event.clientX,
        startY: event.clientY,
        lastX: event.clientX,
        lastY: event.clientY,
        time: event.timeStamp,
        startBallX: ball.x,
        startBallY: ball.y,
        pointerId: event.pointerId,
        moved: false,
      };
      ball.vx = 0;
      ball.vy = 0;
      node.setPointerCapture(event.pointerId);
      this.wake();
    });

    node.addEventListener('pointermove', event => {
      const drag = this.drag;
      if (drag?.ball !== ball || drag.pointerId !== event.pointerId) return;
      const dx = event.clientX - drag.startX;
      const dy = event.clientY - drag.startY;
      drag.moved ||= Math.hypot(dx, dy) > 5;
      if (!drag.moved) return;

      ball.x = clamp(drag.startBallX + dx, ball.radius, this.width - ball.radius);
      ball.y = clamp(drag.startBallY + dy, ball.radius, this.height - ball.radius);
      const dt = Math.max(.008, (event.timeStamp - drag.time) / 1000);
      ball.vx = clamp((event.clientX - drag.lastX) / dt, -1050, 1050);
      ball.vy = clamp((event.clientY - drag.lastY) / dt, -1050, 1050);
      drag.lastX = event.clientX;
      drag.lastY = event.clientY;
      drag.time = event.timeStamp;
      this.draw();
    });

    const release = event => {
      const drag = this.drag;
      if (drag?.ball !== ball || drag.pointerId !== event.pointerId) return;
      suppressClick = drag.moved;
      this.drag = null;
      if (event.type !== 'pointerup' || event.timeStamp - drag.time > 100) {
        ball.vx = 0;
        ball.vy = 0;
      }
      this.wake();
    };
    node.addEventListener('pointerup', release);
    node.addEventListener('pointercancel', release);
    node.addEventListener('lostpointercapture', release);
    node.addEventListener('click', event => {
      if (!suppressClick || event.detail === 0) this.select(ball.item.id);
      suppressClick = false;
    });
  }
}
