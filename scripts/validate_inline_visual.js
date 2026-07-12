const fs = require("fs");

const file = process.argv[2];
if (!file) {
  throw new Error("Pass an inline visualization HTML path.");
}
const source = fs.readFileSync(file, "utf8");
const blocks = [...source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
const inline = blocks.filter((block) => block[1].trim());
for (const block of inline) {
  new Function(block[1]);
}
if ((source.match(/id="baku-ev-accessibility"/g) || []).length !== 1) {
  throw new Error("Visualization root ID is missing or duplicated.");
}
if (source.includes('\\"')) {
  throw new Error("Fragment contains literal escaped quotes.");
}
console.log(`inline_scripts=${inline.length} syntax=ok bytes=${Buffer.byteLength(source)}`);
