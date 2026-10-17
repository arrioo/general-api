module.exports = {
  apps: [
    {
      name: "general-api",
      // script: "./venv/bin/uvicorn",        // run the binary directly...
      script: "uvicorn",        // run the binary using laragon
      interpreter: "none",                  // ...so PM2 must NOT prefix it with an interpreter
      args: "app.main:app --host 0.0.0.0 --port 8000",
      cwd: __dirname,
      autorestart: true,
    },
    {
      name: "general-worker",
      script: "worker/worker.js",
      cwd: __dirname,
      instances: 1,                         // MUST stay 1 for strict one-by-one processing
      exec_mode: "fork",
      autorestart: true,
    },
  ],
};
