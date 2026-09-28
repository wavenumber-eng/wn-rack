//! L0_007 (Rust): parse_duration against the cases in
//! L0_units/vectors/L0_001_parse_duration.json.

use rust_durations::parse_duration;

const VECTORS: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../L0_units/vectors/L0_001_parse_duration.json"
);

#[test]
fn l0_007_plain_parse() {
    let text = std::fs::read_to_string(VECTORS).expect("read the vector file");
    let vectors: serde_json::Value = serde_json::from_str(&text).expect("parse the vector file");
    let mut failures = Vec::new();
    for case in vectors["cases"].as_array().expect("cases") {
        let id = case["id"].as_str().expect("id");
        let input = case["inputs"]["text"].as_str().expect("inputs.text");
        let expected = case["expect"]["seconds"].as_u64().expect("expect.seconds");
        let actual = parse_duration(input);
        if actual != expected {
            failures.push(format!("{id}: expected {expected}, got {actual}"));
        }
    }
    assert!(failures.is_empty(), "{} case(s) failed:\n{}", failures.len(), failures.join("\n"));
}
