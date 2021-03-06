from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
from uuid import uuid4
from flask import Flask, request, render_template, send_from_directory

import argparse
import os.path
import re
import sys
import tarfile

import numpy as np
from six.moves import urllib
import tensorflow as tf
from PIL import Image

app = Flask(__name__)

FLAGS = None

# pylint: disable=line-too-long
DATA_URL = 'http://download.tensorflow.org/models/image/imagenet/inception-2015-12-05.tgz'
# pylint: enable=line-too-long

#####################################################################
### MODEL RELATED FUNCTION DEFINITIONS AND VARIABLES BEGIN HERE
#####################################################################

#### global declaration of graph definition
graph1 = tf.Graph()
graph_def = None


#######
# NodeLookup is used to convert the output layer ids into human readable format
# which are same as those present in the INCEPTION V3 models
#######

class NodeLookup(object):
  """Converts integer node ID's to human readable labels."""

  def __init__(self,
               label_lookup_path=None,
               uid_lookup_path=None):
    if not label_lookup_path:
      label_lookup_path = os.path.join(
          FLAGS.model_dir, 'imagenet_2012_challenge_label_map_proto.pbtxt')
    if not uid_lookup_path:
      uid_lookup_path = os.path.join(
          FLAGS.model_dir, 'imagenet_synset_to_human_label_map.txt')
    self.node_lookup = self.load(label_lookup_path, uid_lookup_path)

  def load(self, label_lookup_path, uid_lookup_path):
    """Loads a human readable English name for each softmax node.
    Args:
      label_lookup_path: string UID to integer node ID.
      uid_lookup_path: string UID to human-readable string.
    Returns:
      dict from integer node ID to human-readable string.
    """
    if not tf.gfile.Exists(uid_lookup_path):
      tf.logging.fatal('File does not exist %s', uid_lookup_path)
    if not tf.gfile.Exists(label_lookup_path):
      tf.logging.fatal('File does not exist %s', label_lookup_path)

    # Loads mapping from string UID to human-readable string
    proto_as_ascii_lines = tf.gfile.GFile(uid_lookup_path).readlines()
    uid_to_human = {}
    p = re.compile(r'[n\d]*[ \S,]*')
    for line in proto_as_ascii_lines:
      parsed_items = p.findall(line)
      uid = parsed_items[0]
      human_string = parsed_items[2]
      uid_to_human[uid] = human_string

    # Loads mapping from string UID to integer node ID.
    node_id_to_uid = {}
    proto_as_ascii = tf.gfile.GFile(label_lookup_path).readlines()
    for line in proto_as_ascii:
      if line.startswith('  target_class:'):
        target_class = int(line.split(': ')[1])
      if line.startswith('  target_class_string:'):
        target_class_string = line.split(': ')[1]
        node_id_to_uid[target_class] = target_class_string[1:-2]

    # Loads the final mapping of integer node ID to human-readable string
    node_id_to_name = {}
    for key, val in node_id_to_uid.items():
      if val not in uid_to_human:
        tf.logging.fatal('Failed to locate: %s', val)
      name = uid_to_human[val]
      node_id_to_name[key] = name

    return node_id_to_name

  def id_to_string(self, node_id):
    if node_id not in self.node_lookup:
      return ''
    return self.node_lookup[node_id]

######
# This creates graph from .pb files. here however graph_def is global
# thus the graph is built only once at the start of the server and graph1
# is made the default graph and is used in all sessions for prediction
######

def create_graph():
  """Creates a graph from saved GraphDef file and returns a saver."""
  # Creates graph from saved graph_def.pb.
  with tf.gfile.FastGFile(os.path.join(
      FLAGS.model_dir, 'classify_image_graph_def.pb'), 'rb') as f:
    graph_def = tf.GraphDef()
    graph_def.ParseFromString(f.read())
    with graph1.as_default():
        _ = tf.import_graph_def(graph_def, name='')

######
# converts image into 100*100 size and then provides inference using the graph1
# graph in every query sessions
######

def run_inference_on_image(image):
  """Runs inference on an image.
  Args:
    image: Image file name.
  Returns:
    Nothing
  """
  if not tf.gfile.Exists(image):
    tf.logging.fatal('File does not exist %s', image)
  img = Image.open(image)
  new_image = img.resize((100, 100))
  new_image.save(image)
  image_data = tf.gfile.FastGFile(image, 'rb').read()
  #print("--------------")
  #print(type(image_data))
  #print("--------------")
  #print(image_data)

  with tf.Session( graph = graph1 ) as sess:
    # Some useful tensors:
    # 'softmax:0': A tensor containing the normalized prediction across
    #   1000 labels.
    # 'pool_3:0': A tensor containing the next-to-last layer containing 2048
    #   float description of the image.
    # 'DecodeJpeg/contents:0': A tensor containing a string providing JPEG
    #   encoding of the image.
    # Runs the softmax tensor by feeding the image_data as input to the graph.
    softmax_tensor = sess.graph.get_tensor_by_name('softmax:0')
    predictions = sess.run(softmax_tensor,
                           {'DecodeJpeg/contents:0': image_data})
    predictions = np.squeeze(predictions)

    preds = []
    # Creates node ID --> English string lookup.
    node_lookup = NodeLookup()
    top_k = predictions.argsort()[-FLAGS.num_top_predictions:][::-1]
    for node_id in top_k:
      human_string = node_lookup.id_to_string(node_id)
      score = predictions[node_id]
      pred = {}
      pred[human_string] = score*100
      #print(" ")
      #print(pred)

      #print('%s (score = %.5f)' % (human_string, score))
      preds.append(pred)

  return preds

########
# used for getting the .tgz file where inception is stored and then all the files are
# extracted and then the .pb file is used to create graph in the create_graph print_function
########

def maybe_download_and_extract():
  """Download and extract model tar file."""
  dest_directory = FLAGS.model_dir
  if not os.path.exists(dest_directory):
    os.makedirs(dest_directory)
  filename = DATA_URL.split('/')[-1]
  filepath = os.path.join(dest_directory, filename)
  if not os.path.exists(filepath):
    def _progress(count, block_size, total_size):
      sys.stdout.write('\r>> Downloading %s %.1f%%' % (
          filename, float(count * block_size) / float(total_size) * 100.0))
      sys.stdout.flush()
    filepath, _ = urllib.request.urlretrieve(DATA_URL, filepath, _progress)
    print()
    statinfo = os.stat(filepath)
    print('Successfully downloaded', filename, statinfo.st_size, 'bytes.')
  tarfile.open(filepath, 'r:gz').extractall(dest_directory)

################################################################

# MODEL FUNCTION DEFINITIONS ENDS HERE

################################################################

################################################################

# flask SERVER FUNCTION DEFINITIONS BEGIN HERE

################################################################

APP_ROOT = os.path.dirname(os.path.abspath(__file__))

@app.route("/")
def index():
    return render_template("upload.html")

@app.route("/upload", methods=["POST"])
def upload():
    filenames = []
    preds = []
    target = os.path.join(APP_ROOT, 'images/')
    print(target)
    if not os.path.isdir(target):
            os.mkdir(target)
    else:
        print("Couldn't create upload directory: {}".format(target))
    print(request.files.getlist("file"))
    for upload in request.files.getlist("file"):
        print(upload)
        print("{} is the file name".format(upload.filename))
        filename = upload.filename
        filenames.append(filename)
        destination = "/".join([target, filename])
        print ("Accept incoming file:", filename)
        print ("Save it to:", destination)
        upload.save(destination)
        ######

        #image = (FLAGS.image_file if FLAGS.image_file else
        #         os.path.join(FLAGS.model_dir, 'house.jpg'))
        pred = run_inference_on_image(destination)
        ######
        preds.append(pred)
    rng = range(0,len(preds))

    return render_template("complete.html", image_name=filenames, preds = preds, rng = rng)

@app.route('/upload/<filename>')
def send_image(filename):
    return send_from_directory("images", filename)

###################################################
# SERVER DEFINITIONS ENDS
###################################################

if __name__ == "__main__":
    #################
    parser = argparse.ArgumentParser()
    # classify_image_graph_def.pb:
    #   Binary representation of the GraphDef protocol buffer.
    # imagenet_synset_to_human_label_map.txt:
    #   Map from synset ID to a human readable string.
    # imagenet_2012_challenge_label_map_proto.pbtxt:
    #   Text representation of a protocol buffer mapping a label to synset ID.
    parser.add_argument(
        '--model_dir',
        type=str,
        default='C:/Users/VISHAL ASHANK/project/inception/imgrecog',
        help="""\
        Path to classify_image_graph_def.pb,
        imagenet_synset_to_human_label_map.txt, and
        imagenet_2012_challenge_label_map_proto.pbtxt.\
        """
    )
    parser.add_argument(
        '--image_file',
        type=str,
        default='',
        help='Absolute path to image file.'
    )
    parser.add_argument(
        '--num_top_predictions',
        type=int,
        default=5,
        help='Display this many predictions.'
    )
    FLAGS, unparsed = parser.parse_known_args()

    # extract the .pb file for model graph creation
    maybe_download_and_extract()

    # create the model graph
    create_graph()

    # start the SERVER on port 8080
    app.run(port=8080, debug=True)
