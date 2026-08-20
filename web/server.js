const express = require('express');
const path = require('path');
const jwt = require('jsonwebtoken');
const cookieParser = require('cookie-parser');
const { createProxyMiddleware } = require('http-proxy-middleware');

const app = express();
app.set('trust proxy', true);
app.use(cookieParser());

const PORT = process.env.PORT || 3000;
const API_URL = process.env.API_URL || 'http://127.0.0.1:8000';
const JWT_SECRET = process.env.JWT_SECRET || 'dev-secret-change-me';
const COOKIE_NAME = 'session';

const KEEPALIVE_MS = 10 * 60 * 1000;
const JST_WINDOW_START = 11;
const JST_WINDOW_END = 22;

const inJstWindow = () => {
  const jst = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Tokyo' }));
  return jst.getHours() >= JST_WINDOW_START && jst.getHours() <= JST_WINDOW_END;
};

const warmApi = async () => {
  if (!inJstWindow()) return;
  try {
    await fetch(`${API_URL}/api/health`, { signal: AbortSignal.timeout(120000) });
  } catch (err) {
    console.log(`api keep-alive failed: ${err.message}`);
  }
};

const noCache = (req, res, next) => {
  res.set('Cache-Control', 'no-cache, no-store, must-revalidate');
  res.set('Pragma', 'no-cache');
  res.set('Expires', '0');
  next();
};

const isAuthed = (req) => {
  const token = req.cookies && req.cookies[COOKIE_NAME];
  if (!token) return false;
  try {
    jwt.verify(token, JWT_SECRET);
    return true;
  } catch (_) {
    return false;
  }
};

app.use(noCache);

app.get('/ping', (req, res) => {
  res.status(200).send('ok');
});

app.get('/wake', (req, res) => {
  warmApi();
  res.status(200).send('ok');
});

app.get('/login', (req, res) => {
  if (isAuthed(req)) return res.redirect('/');
  res.sendFile(path.join(__dirname, 'public', 'login.html'));
});

app.get('/', (req, res) => {
  if (!isAuthed(req)) return res.redirect('/login');
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.use('/api', createProxyMiddleware({
  target: API_URL,
  changeOrigin: true,
  xfwd: true,
}));

app.get('*', (req, res) => {
  const token = req.cookies && req.cookies[COOKIE_NAME];
  if (!token || !isAuthed(req)) return res.redirect('/login');
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, () => {
  console.log(`Frontend server running on http://localhost:${PORT}`);
  console.log(`Proxying API requests to ${API_URL}`);
  warmApi();
  setInterval(warmApi, KEEPALIVE_MS);
});