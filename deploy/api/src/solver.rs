use image::ImageReader;
use ndarray::Array4;
use ort::session::{Session, builder::GraphOptimizationLevel};
use ort::value::Tensor;
use std::io::Cursor;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::UnixListener;

const CHARS: &str = "0123456789abcdefghijklmnopqrstuvwxyz";
const SOCKET_PATH: &str = "/tmp/captop-solver.sock";
// mirrors the api's own cap before it writes the prefix; a foreign/local
// process claiming u32::MAX would otherwise allocate 4GB on first read
const MAX_IMAGE_BYTES: u32 = 256 * 1024;

#[tokio::main]
async fn main() {
    // Clean up stale socket
    let _ = std::fs::remove_file(SOCKET_PATH);

    let model_path =
        std::env::var("MODEL_PATH").unwrap_or_else(|_| "../model/captcha_model.onnx".to_string());

    let env = ort::init().build().unwrap();
    // ponytail: round-robin pool, no backpressure; SOLVER_SESSIONS if contention
    // ever shows up in the metrics
    let pool_size = std::env::var("SOLVER_SESSIONS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(4);
    let sessions: Vec<_> = (0..pool_size)
        .map(|_| {
            let session = Session::builder(&env)
                .unwrap()
                .with_optimization_level(GraphOptimizationLevel::Level3)
                .unwrap()
                .commit_from_file(&model_path)
                .unwrap();
            Arc::new(Mutex::new(session))
        })
        .collect();
    let next = AtomicUsize::new(0);

    let listener = UnixListener::bind(SOCKET_PATH).unwrap();
    println!(
        "Solver listening on {} ({} sessions)",
        SOCKET_PATH, pool_size
    );

    loop {
        let (stream, _) = listener.accept().await.unwrap();
        let i = next.fetch_add(1, Ordering::Relaxed) % sessions.len();
        let session = Arc::clone(&sessions[i]);

        tokio::spawn(async move {
            if let Err(e) = handle_connection(stream, &session).await {
                eprintln!("Solver error: {}", e);
            }
        });
    }
}

async fn handle_connection(
    mut stream: tokio::net::UnixStream,
    session: &Arc<Mutex<Session>>,
) -> Result<(), Box<dyn std::error::Error>> {
    // Read 4-byte length prefix (little-endian u32)
    let len = stream.read_u32_le().await?;
    if len > MAX_IMAGE_BYTES {
        return Err(format!("image too large: {len}B").into());
    }

    // Read image bytes
    let mut image_data = vec![0u8; len as usize];
    stream.read_exact(&mut image_data).await?;

    let text = {
        let mut session = session.lock().unwrap();
        let tensor = preprocess(&image_data)?;
        let input = Tensor::from_array(tensor)?;
        let outputs = session.run(ort::inputs!["image" => input])?;
        let (_, raw) = outputs["output"].try_extract_tensor::<f32>()?;
        ctc_decode(raw)
    };

    // Write result back
    stream.write_all(text.as_bytes()).await?;
    stream.shutdown().await?;

    Ok(())
}

fn preprocess(data: &[u8]) -> Result<Array4<f32>, Box<dyn std::error::Error>> {
    let img = ImageReader::new(Cursor::new(data))
        .with_guessed_format()?
        .decode()?;

    let gray = img.to_luma8();
    let resized = image::imageops::resize(&gray, 200, 40, image::imageops::FilterType::Lanczos3);

    let normalized: Vec<f32> = resized
        .iter()
        .map(|&p| (p as f32 / 255.0 - 0.5) / 0.5)
        .collect();

    Ok(Array4::from_shape_vec((1, 1, 40, 200), normalized)?)
}

fn ctc_decode(output: &[f32]) -> String {
    let vocab_size = 37;
    let time_steps = 50;
    let mut result = String::new();
    let mut prev_idx = 0usize;

    for t in 0..time_steps {
        let start = t * vocab_size;
        let logits = &output[start..start + vocab_size];

        let max_logit = logits.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
        let probs: Vec<f32> = logits.iter().map(|x| (x - max_logit).exp()).collect();
        let idx = probs
            .iter()
            .enumerate()
            .max_by(|a, b| a.1.partial_cmp(b.1).unwrap())
            .unwrap()
            .0;

        if idx != 0
            && idx != prev_idx
            && let Some(c) = CHARS.chars().nth(idx - 1)
        {
            result.push(c);
        }
        prev_idx = idx;
    }

    result.to_uppercase()
}

#[cfg(test)]
mod tests {
    use super::*;

    const VOCAB: usize = 37;

    fn output_with(steps: &[(usize, usize)]) -> Vec<f32> {
        let mut output = vec![0f32; VOCAB * 50];
        for t in 0..50 {
            output[t * VOCAB] = 10.0;
        }
        for &(t, idx) in steps {
            output[t * VOCAB + idx] = 20.0;
        }
        output
    }

    #[test]
    fn ctc_decode_dedupes_and_skips_blank() {
        let output = output_with(&[(0, 1), (1, 1), (2, 0), (3, 2), (4, 1)]);
        assert_eq!(ctc_decode(&output), "010");
    }

    #[test]
    fn ctc_decode_all_blank_is_empty() {
        let output = output_with(&[]);
        assert_eq!(ctc_decode(&output), "");
    }

    #[test]
    fn ctc_decode_repeats_needing_separator() {
        let output = output_with(&[(0, 1), (1, 0), (2, 1)]);
        assert_eq!(ctc_decode(&output), "00");
    }
}
