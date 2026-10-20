const path = require("path");
require("dotenv").config({ path: path.join(__dirname, "..", ".env") });

const { Worker } = require("bullmq");
const IORedis = require("ioredis");
const { Pool } = require("pg");

const db = new Pool({
  connectionString: process.env.DATABASE_URL,
  options: `-c search_path=${process.env.DATABASE_SCHEMA}`,
});

const connection = new IORedis(process.env.REDIS_URL || "redis://localhost:6379", {
  maxRetriesPerRequest: null, // required by BullMQ workers
});

async function setStatus(client, orderId, from, to, note) {
  await client.query("UPDATE orders SET status = $2 WHERE id = $1", [orderId, to]);
  await client.query(
    `INSERT INTO order_status_history (order_id, from_status, to_status, note)
     VALUES ($1, $2, $3, $4)`,
    [orderId, from, to, note || null]
  );
}

async function markFailed(orderId, reason) {
  const client = await db.connect();
  try {
    await client.query("BEGIN");
    const { rows } = await client.query(
      "SELECT status FROM orders WHERE id = $1 FOR UPDATE", [orderId]);
    const cur = rows[0] && rows[0].status;
    if (cur === "queued" || cur === "processing") {
      await setStatus(client, orderId, cur, "failed", reason);
      await client.query("UPDATE orders SET fail_reason = $2 WHERE id = $1", [orderId, reason]);
    }
    await client.query("COMMIT");
  } catch (e) {
    await client.query("ROLLBACK");
    console.error("markFailed error:", e.message);
  } finally {
    client.release();
  }
}

async function processOrder(orderId) {
  const client = await db.connect();
  try {
    await client.query("BEGIN");

    // Lock the order row: serialises with POST /orders/{id}/cancel
    const { rows } = await client.query(
      "SELECT id, user_id, warehouse_id, status FROM orders WHERE id = $1 FOR UPDATE",
      [orderId]
    );
    const order = rows[0];
    if (!order) throw new Error(`Order ${orderId} not found`);
    if (order.status !== "queued") {          // cancelled or already processed
      await client.query("ROLLBACK");
      return { skipped: true, status: order.status };
    }

    await setStatus(client, orderId, "queued", "processing");

    // ORDER BY product_id => consistent lock order => no deadlocks
    const items = (await client.query(
      "SELECT id, product_id, qty FROM order_items WHERE order_id = $1 ORDER BY product_id",
      [orderId]
    )).rows;

    for (const it of items) {
      await client.query(
        `SELECT apply_stock_movement($1, $2, 'sale', $3, 'order', $4, $5, $6, NULL)`,
        [it.product_id, order.warehouse_id, -it.qty, orderId, order.user_id,
         `order-${orderId}-item-${it.id}`]
      );
    }

    await client.query("UPDATE orders SET processed_at = now() WHERE id = $1", [orderId]);
    await setStatus(client, orderId, "processing", "confirmed", "Stock deducted");
    await client.query("COMMIT");
    return { success: true };
  } catch (e) {
    await client.query("ROLLBACK");
    if (String(e.message).includes("INSUFFICIENT_STOCK")) {
      // business failure: do NOT retry
      await markFailed(orderId, "out_of_stock");
      return { success: false, reason: "out_of_stock" };
    }
    throw e; // technical error: BullMQ retries (attempts set by the API)
  } finally {
    client.release();
  }
}

const worker = new Worker(
  process.env.QUEUE_NAME || "order-queue",
  async (job) => processOrder(job.data.orderId),
  { connection, concurrency: 1 } // strictly one order at a time
);

worker.on("completed", (job, res) => console.log(`job ${job.id} done`, res));
worker.on("failed", async (job, err) => {
  console.error(`job ${job && job.id} failed (attempt ${job && job.attemptsMade}):`, err.message);
  // retries exhausted -> make sure the order does not stay 'queued' forever
  if (job && job.attemptsMade >= (job.opts.attempts || 1)) {
    await markFailed(job.data.orderId, "error");
  }
});

process.on("SIGTERM", async () => { await worker.close(); await db.end(); process.exit(0); });
process.on("SIGINT",  async () => { await worker.close(); await db.end(); process.exit(0); });
console.log("worker started");