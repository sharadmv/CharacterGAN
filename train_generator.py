import numpy as np
import cPickle as pickle
import theano
import sys
import csv
import logging
import random
from dataset import *
from deepx.nn import *
from deepx.rnn import *
from deepx.loss import *
from deepx.optimize import *
from argparse import ArgumentParser
theano.config.on_unused_input = 'ignore'

logging.basicConfig(level=logging.DEBUG)


def parse_args():
	argparser = ArgumentParser()
	argparser.add_argument("reviews")
	argparser.add_argument("--log", default="loss/generator_loss_current.txt")
	return argparser.parse_args()


class WindowedBatcher(object):

	def __init__(self, sequences, encodings, batch_size=100, sequence_length=50):
		self.sequences = sequences

		self.pre_vector_sizes = [c.seq[0].shape[0] for c in self.sequences]
		self.pre_vector_size = sum(self.pre_vector_sizes)

		self.encodings = encodings
		self.vocab_sizes = [c.index for c in self.encodings]
		self.vocab_size = sum(self.vocab_sizes)
		self.batch_index = 0
		self.batches = []
		self.batch_size = batch_size
		self.sequence_length = sequence_length + 1
		self.length = len(self.sequences[0])

		self.batch_index = 0
		self.X = np.zeros((self.length, self.pre_vector_size))
		self.X = np.hstack([c.seq for c in self.sequences])

		N, D = self.X.shape
		assert N > self.batch_size * self.sequence_length, "File has to be at least %u characters" % (self.batch_size * self.sequence_length)

		self.X = self.X[:N - N % (self.batch_size * self.sequence_length)]
		self.N, self.D = self.X.shape
		self.X = self.X.reshape((self.N / self.sequence_length, self.sequence_length, self.D))

		self.N, self.S, self.D = self.X.shape

		self.num_sequences = self.N / self.sequence_length
		self.num_batches = self.N / self.batch_size
		self.batch_cache = {}

	def next_batch(self):
		idx = (self.batch_index * self.batch_size)
		if self.batch_index >= self.num_batches:
			self.batch_index = 0
			idx = 0

		if self.batch_index in self.batch_cache:
			batch = self.batch_cache[self.batch_index]
			self.batch_index += 1
			return batch

		X = self.X[idx:idx + self.batch_size]
		y = np.zeros((X.shape[0], self.sequence_length, self.vocab_size))
		for i in xrange(self.batch_size):
			for c in xrange(self.sequence_length):
				seq_splits = np.split(X[i, c], np.cumsum(self.pre_vector_sizes))
				vec = np.concatenate([e.convert_representation(split) for
									  e, split in zip(self.encodings, seq_splits)])
				y[i, c] = vec

		X = y[:, :-1, :]
		y = y[:, 1:, :self.vocab_sizes[0]]


		X = np.swapaxes(X, 0, 1)
		y = np.swapaxes(y, 0, 1)
		# self.batch_cache[self.batch_index] = X, y
		self.batch_index += 1
		return X, y

def generate_sample(length):
	'''Generate a sample from the current version of the generator'''
	characters = [np.array([0])]
	generator2.reset_states()
	for i in xrange(length):
		output = generator2.predict(np.eye(len(text_encoding))[None, characters[-1]])
		sample = np.random.choice(xrange(len(text_encoding)), p=output[0, 0])
		characters.append(np.array([sample]))
	characters =  np.array(characters).ravel()
	num_seq  = NumberSequence(characters[1:])
	return num_seq.decode(text_encoding)


if __name__ == '__main__':
	args = parse_args()
	
	logging.debug('Reading file...')
	with open(args.reviews, 'r') as f:
		reviews = [r[3:] for r in f.read().strip().split('\n')]
		reviews = [r.replace('\x05',  '') for r in reviews]
		reviews = [r.replace('<STR>', '') for r in reviews]

	logging.debug('Retrieving text encoding...')
	with open('data/charnet-encoding.pkl', 'rb') as fp:
		text_encoding = pickle.load(fp)

	# Create reviews and targets
	logging.debug('Converting to one-hot...')
	review_sequences = [CharacterSequence.from_string(r) for r in reviews]
	num_sequences    = [c.encode(text_encoding) for c in review_sequences]
	final_sequences  = NumberSequence(np.concatenate([c.seq.astype(np.int32) for c in num_sequences]))

	# Construct the batcher
	batcher = WindowedBatcher([final_sequences], [text_encoding], sequence_length=200, batch_size=100)
	generator = Sequence(Vector(len(text_encoding), batch_size=100)) >> Repeat(LSTM(1024, stateful=True), 2) >> Softmax(len(text_encoding))
	generator2 = Sequence(Vector(len(text_encoding), batch_size=1)) >> Repeat(LSTM(1024, stateful=True), 2) >> Softmax(len(text_encoding))

	logging.debug('Loading prior model...')
	with open('models/generative/generative-model-0.0.renamed.pkl', 'rb') as fp:
		generator.set_state(pickle.load(fp))
	with open('models/generative/generative-model-0.0.renamed.pkl', 'rb') as fp:
		generator2.set_state(pickle.load(fp))
	
	# Optimization procedure
	rmsprop = RMSProp(generator, CrossEntropy())

	def train_generator(iterations, step_size):
		with open(args.log, 'w') as f:
			for _ in xrange(iterations):
				X, y = batcher.next_batch()
				loss = rmsprop.train(X, y, step_size)
				print >> f, 'Loss[%u]: %f' % (_, loss)
				print 'Loss[%u]: %f' % (_, loss)
				f.flush()

		with open('models/generative/generative-model-current.pkl', 'wb') as g:
			pickle.dump(generator.get_state(), g)

		generator2.set_state(generator.get_state())
