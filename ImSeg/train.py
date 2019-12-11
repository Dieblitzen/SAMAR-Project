import sys
sys.path.append('.')
import json
from ImSeg_Dataset import ImSeg_Dataset
import ImSeg.refine_net as refine_net

import os
import logging
import argparse
import numpy as np
import tensorflow as tf


## Supported model variants, along with model loading function given a config dictionary.
MODEL_TYPES = {"RefineNet": refine_net.refine_net_from_config}


def passed_arguments():
  parser = argparse.ArgumentParser(description="Script to train an Image Segmentation model.")
  parser.add_argument('--data_path',
                      type=str,
                      required=True,
                      help='Path to directory where extracted dataset is stored.')
  parser.add_argument('--config',
                      type=str,
                      required=True,
                      help='Path to model config .json file defining model hyperparams.')
  parser.add_argument('--classes_path',\
                      type=str,
                      default='./classes.json',
                      help='Path to directory where extracted dataset is stored.')
  args = parser.parse_args()
  return args


"""
Initialises image segmentation model given a config dictionary.
Requires: 
  model_type: A name from the dictionary `MODEL_TYPES` in `train.py`.
  config: A valid config dictionary for the type of model
"""
def model_from_config(model_type, config):
  assert model_type in MODEL_TYPES, "Input model type is not supported yet."
  model = MODEL_TYPES[model_type](config)
  return model


"""
Instantiates loss function and optimizer based on name and kwargs.
Ensure that names are valid in the tf.keras.losses/optmizers modules.
Also ensure keyword arguments match.
Defaults to using BinaryCrossentropy (from logits), and Adam(lr=0.0001)
"""
def get_loss_optimizer(config):
  loss_name = config.get("loss", "BinaryCrossentropy")
  loss_kwargs = config.get("loss_kwargs", {"from_logits":True})
  optimizer_name = config.get("optimizer", "Adam")
  optimizer_kwargs = config.get("optimizer_kwargs", {"learning_rate":0.0001})

  try:
    loss_function = tf.keras.losses.__dict__[loss_name](**loss_kwargs)
  except KeyError:
    raise ValueError("Loss name (case sensitive) doesn't match keras losses.")

  try:
    optimizer = tf.keras.optimizers.__dict__[optimizer_name](**optimizer_kwargs)
  except KeyError:
    raise ValueError("Optimizer name (case sensitive) doesn't match keras optimizers.")
  return loss_function, optimizer


"""
Calculate IoU, Precision and Recall per class for entire batch of images.
Requires:
  preds: model preds array, shape (batch, h, w, #c)
  label_masks: ground truth masks, shape (batch, h, w, #c)
  pred_threshold: Confidence threshold over which pixel prediction counted,
"""
def calculate_iou_prec_recall(preds, label_masks, pred_threshold=0.0):
  # Reduce dimensions across all but classes dimension.
  preds = preds.reshape(-1, preds.shape[-1])
  label_masks = label_masks.reshape(-1, label_masks.shape[-1])

  preds = preds > pred_threshold
  intersection = np.logical_and(preds, label_masks)
  union = np.logical_or(preds, label_masks)
  iou_scores = np.sum(intersection, axis=0) / np.sum(union, axis=0)
  iou_scores[np.isnan(iou_scores)] = 0.0

  precision = np.sum(intersection, axis=0)/np.sum(preds, axis=0)
  precision[np.isnan(precision)] = 0.0

  recall = np.sum(intersection, axis=0)/np.sum(label_masks, axis=0)
  recall[np.isnan(recall)] = 0.0

  return iou_scores, precision, recall


"""
Logs metrics to tensorflow summary writers, and to a log file.
Also prints mean metrics for the epoch
Requires:
  metrics_dict: Pairs of metric_name (make it informative!), metric_value_number
  writer: Either training or validation summary_writer
  phase: Either 'train' or 'val'
"""
def log_metrics(metrics_dict, writer, epoch, phase):
  # Do it separately for tf writers.
  with writer.as_default():
    for metric_name, metric_value in metrics_dict.items():
      tf.summary.scalar(metric_name, metric_value, step=epoch+1)

  print(f"Phase: {phase}")
  for metric_name, metric_value in metrics_dict.items():
    logging.info(f"Epoch {epoch+1}, Phase {phase}, {metric_name}: {metric_value}")
    
    if not metric_name.startswith('class'):
      print(f"{metric_name}: {metric_value}")


"""
Performs one training step over a batch.
Passes one batch of images through the model, and backprops the gradients.
"""
@tf.function
def train_step(model, loss_function, train_loss, optimizer, images, labels):
  with tf.GradientTape() as tape:
    preds = model(images)
    loss = loss_function(labels, preds)

  gradients = tape.gradient(loss, model.trainable_variables)
  optimizer.apply_gradients(zip(gradients, model.trainable_variables))

  train_loss.update_state(loss)

  return preds

"""
Performs one validation step over a batch.
"""
@tf.function
def val_step(model, loss_function, val_loss, optimizer, images, labels):
  preds = model(images)
  loss = loss_function(labels, preds)

  val_loss.update_state(loss)

  return preds


if __name__ == "__main__":
  args = passed_arguments()

  # Get args from config.
  config_path = args.config
  with open(config_path, 'r') as f:
    config = json.load(f)
  model_type = config.get("type", "RefineNet")
  model_name = config["name"]
  epochs = config["epochs"]
  batch_size = config["batch_size"]
  augment_kwargs = config.get("augment", {})

  ## Set up dataset, number of train/val samples, number of batches and interested classes.
  dataset = ImSeg_Dataset(data_path=args.data_path, classes_path=args.classes_path,
                          augment_kwargs=augment_kwargs)
  if dataset.data_sizes[0] == 0:
    dataset.build_dataset()
  num_train, num_val = dataset.data_sizes[0], dataset.data_sizes[1]
  num_train_batches, num_val_batches = num_train//batch_size, num_val//batch_size
  config["classes"] = dataset.seg_classes if not config["classes"] else config["classes"]
  interest_classes = config["classes"]

  # Create model output dir where checkpoints/metrics etc will be stored. Save config here.
  dataset.create_model_out_dir(model_name)
  with open(os.path.join(dataset.model_path, 'config.json'), 'w') as f:
    json.dump(config, f, indent=2)

  ## Summary writers for training/validation and logger
  train_summary_writer = tf.summary.create_file_writer(os.path.join(dataset.metrics_path, 'train'))
  val_summary_writer = tf.summary.create_file_writer(os.path.join(dataset.metrics_path, 'val'))
  logging.basicConfig(filename=os.path.join(dataset.metrics_path, f"{model_name}.log"), level=logging.INFO)

  ## Set up model from config.
  model = model_from_config(model_type, config)

  ## Get loss and optimizer from config
  loss_function, optimizer = get_loss_optimizer(config)

  ## =============================================================================================
  ## BEGIN ITERATING OVER EPOCHS
  ## =============================================================================================
  train_loss = tf.keras.metrics.Mean(name='train_loss')
  val_loss = tf.keras.metrics.Mean(name='val_loss')
  best_val_iou = float('-inf')
  
  for epoch in range(epochs):
    print(f"\nEpoch {epoch+1}")

    train_indices, val_indices = list(range(num_train)), list(range(num_val))
    np.random.shuffle(train_indices)
    np.random.shuffle(val_indices)
    
    # Alternate between training and validation epochs.
    for phase in ["train", "val"]:

      if phase == "train":
        num_batches, indices = num_train_batches, train_indices
        writer = train_summary_writer
        epoch_loss = train_loss 
        feed_model = train_step 
      else:
        num_batches, indices = num_val_batches, val_indices
        writer = val_summary_writer
        epoch_loss = val_loss
        feed_model = val_step

      # Initialise non loss metrics
      epoch_ious = tf.keras.metrics.MeanTensor()
      epoch_prec = tf.keras.metrics.MeanTensor()
      epoch_recall = tf.keras.metrics.MeanTensor()

      # Actual train/val over all batches.
      for batch in range(num_batches):
        img_input, label_masks =\
          dataset.get_batch(indices[batch*batch_size : (batch+1)*batch_size], phase, 
                            classes_of_interset=interest_classes)
        
        # Feed inputs to model
        img_input = np.array(img_input, dtype=np.float32)
        preds = feed_model(model, loss_function, epoch_loss, optimizer, img_input, label_masks)
        
        # Get metrics
        preds = preds.numpy()
        ious, prec, recall = calculate_iou_prec_recall(preds, label_masks, pred_threshold=0.0)

        # Update epoch metrics
        epoch_ious.update_state(ious)
        epoch_prec.update_state(prec)
        epoch_recall.update_state(recall)
      
      # Add loss to metrics 
      metrics_dict = {'epoch_loss':epoch_loss.result(), 
                      'mean_iou':np.mean(epoch_ious.result().numpy()),
                      'mean_prec':np.mean(epoch_prec.result().numpy()), 
                      'mean_recall':np.mean(epoch_recall.result().numpy())}

      # Break down IoU, precision and recall by class
      for i, class_name in enumerate(interest_classes):
        sub_metric_dict = {'iou':epoch_ious, 'prec':epoch_prec, 'recall':epoch_recall}
        for metric_type, metrics in sub_metric_dict.items():
          metrics = metrics.result().numpy()
          class_metric_name = f'class_{class_name}_{metric_type}'
          class_metric = metrics[i]
          metrics_dict[class_metric_name] = class_metric
      
      # Log metrics, print metrics, write metrics to summary_writer
      log_metrics(metrics_dict, writer, epoch, phase)

      # Checkpoint model weights if mean iou is good. Also save model config.
      if phase == 'val' and np.mean(epoch_ious.result().numpy()) >= best_val_iou:
        best_val_iou = np.mean(epoch_ious.result().numpy())
        model.save_weights(os.path.join(dataset.checkpoint_path, model_name))

      # End of epoch, reset metrics
      epoch_loss.reset_states()
      epoch_ious.reset_states()
      epoch_prec.reset_states()
      epoch_recall.reset_states()

    print("\n")