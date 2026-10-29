export const session = "bbaaccdd00112233445566778899aabb";

export function syntheticPacket(sequence, nowSeconds = Number(process.hrtime.bigint()) / 1e9) {
  if (!Number.isSafeInteger(sequence) || sequence < 1 || !Number.isFinite(nowSeconds) || nowSeconds < 0) {
    throw new Error("valid_synthetic_frame_identity_required");
  }
  const points = [];
  for (let index = 0; index < 360; index += 1) {
    const along = index / 359, jitter = Math.sin(index * 1.7 + sequence * .3) * .025;
    points.push(-6 + along * 12, -4 + jitter, 0, -6 + along * 12, 4 + jitter, 0);
    points.push(-6 + jitter, -4 + along * 8, 0, 6 + jitter, -4 + along * 8, 0);
  }
  for (let index = 0; index < 160; index += 1) {
    const angle = index / 160 * Math.PI * 2;
    points.push(2 + Math.cos(angle) * .7, 1 + Math.sin(angle) * .7, .5);
  }
  const count = points.length / 3, packet = Buffer.alloc(192 + count * 24);
  packet.write("LXSPOINT", 0, "ascii");
  packet.writeUInt16LE(1, 8);
  // Live-mode bytes exercise the production decoder, but all input is synthetic.
  packet[10] = 1;
  packet.writeUInt32LE(count, 12);
  packet.writeUInt32LE(count, 16);
  packet.writeBigUInt64LE(BigInt(sequence), 24);
  packet.writeDoubleLE(1000 + sequence * .2, 32);
  packet.writeDoubleLE(nowSeconds, 40);
  Buffer.from(session, "hex").copy(packet, 48);
  for (let index = 0; index < 16; index += 1) packet.writeDoubleLE(index % 5 === 0 ? 1 : 0, 64 + index * 8);
  points.forEach((value, index) => packet.writeDoubleLE(value, 192 + index * 8));
  return packet;
}
