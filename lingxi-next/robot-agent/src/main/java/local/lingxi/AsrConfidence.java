package local.lingxi;

import com.agibot.aiem.sdk.AgentParam;

final class AsrConfidence {
    record Result(Double value, String rejection) {
        boolean accepted() {
            return value != null;
        }
    }

    private AsrConfidence() {}

    static Result read(AgentParam param) {
        Object raw = param == null ? null : param.getObject("confidence");
        if (raw == null) return new Result(null, "asr_confidence_unavailable");
        if (!(raw instanceof Number number)) return new Result(null, "asr_confidence_invalid");
        double value = number.doubleValue();
        if (!Double.isFinite(value) || value < 0 || value > 1) {
            return new Result(null, "asr_confidence_invalid");
        }
        return new Result(value, null);
    }
}
